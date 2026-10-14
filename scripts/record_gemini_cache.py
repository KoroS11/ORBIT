#!/usr/bin/env python3
"""Offline seed cache recorder for Gemini API responses.

Replays scenarios through the full engine at UI replay speed (speed=5.0, window=2.0s)
and records prompt/response pairs into data/seed/gemini_cache_seed.json using
model-independent canonical prompt hashing.

Supports dry-run discovery, atomic saves, resumable execution, and 429 daily quota detection.
NEVER prints or logs API keys.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure generous budgets inside the recorder process
os.environ["ORBIT_LLM_SIGNAL_BUDGET_S"] = "180"
os.environ["ORBIT_GEMINI_MAX_QUEUE_WAIT_S"] = "120"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx
from dotenv import load_dotenv

from orbit._engine import build_engine, start_engine_tasks
from orbit.services.kb import KB
from orbit.services.llm.gemini_client import (
    PROMPT_VERSION,
    GeminiLLMClient,
    compute_seed_key,
)
from orbit.services.scenario_replay import ScenarioReplayService

log = logging.getLogger("orbit.recorder")

KB_PATH = REPO_ROOT / "data" / "kb_seed_entries.json"
DEFAULT_SEED_FILE = REPO_ROOT / "data" / "seed" / "gemini_cache_seed.json"
DEFAULT_SCENARIOS = [
    "iran_counter_c5isr_brigade",
    "beat47",
    "army_multidomain_attack_chain",
]
STAGES = ["attribute_primary", "attribute_redteam", "reconcile", "decide"]


def _load_existing_seed(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        log.warning("Could not read existing seed file %s: %s; starting fresh", path, exc)
    return {}


def _save_seed_atomic(path: Path, data: dict[str, Any], dry_run: bool = False) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _get_dummy_response(call_name: str) -> dict[str, Any]:
    """Canned mock responses conforming to stage schemas for dry-run and mock testing."""
    if call_name == "attribute_redteam":
        return {
            "alternative_actor": "Unknown / Ambiguous",
            "confidence_delta": -0.15,
            "objections": ["Alternative doctrine hypotheses remain plausible."],
            "rationale": "High observable ambiguity across cross-domain sensors.",
        }
    if call_name == "decide":
        return {
            "action": "active_defense_escort",
            "target": "ORBIT-LEO-07",
            "rationale": "Multi-domain pressure requires defensive posture adjustment.",
            "authority": "local",
            "confidence": 0.78,
            "request_packet": None,
        }
    # attribute_primary or reconcile
    return {
        "actor": "Russia",
        "confidence": 0.76,
        "doctrine_match": "kb-rpo-ambiguity-001",
        "evidence": [
            "Signal observables match known doctrine precedents.",
            "kb-attribution-uncertainty-001 applies.",
        ],
        "predicted_next": "Continued inspector dwell.",
        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
    }


class RecordingGeminiClient(GeminiLLMClient):
    """Gemini client intercepting calls to record or return from seed cache."""

    def __init__(
        self,
        kb: KB,
        seed_data: dict[str, Any],
        seed_file: Path,
        *,
        model: str,
        api_key: str,
        dry_run: bool = False,
        max_calls: int = 150,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        thinking_budget: int | None = None,
    ) -> None:
        super().__init__(
            kb,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            thinking_budget=thinking_budget,
            transport=transport,
            rate_limiter=False if transport is not None else None,
            cache=False,       # Bypass local state cache
            seed_cache=False,  # Bypass built-in seed cache; we manage seed_data directly
        )
        self.seed_data = seed_data
        self.seed_file = seed_file
        self.dry_run = dry_run
        self.max_calls = max_calls
        self.current_scenario: str = ""
        self.current_pass: int = 1
        self.new_keys_this_pass: int = 0
        self.live_calls_this_pass: int = 0
        self.total_live_calls: int = 0
        self.daily_quota_hit: bool = False
        self.pass_incomplete: bool = False
        self.fallback_count: int = 0
        self.used_keys_this_pass: set[str] = set()
        # scenario -> stage -> set of canonical prompt keys
        self.keys_by_stage: dict[str, dict[str, set[str]]] = {}

    def start_scenario(self, scenario_name: str, pass_num: int = 1) -> None:
        self.current_scenario = scenario_name
        self.current_pass = pass_num

    async def _chat(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        call_name: str | None = None,
    ) -> dict[str, Any]:
        effective_call = call_name or self._current_call_name
        full_prompt = (
            f"{user}\n\n"
            "Reply with a single JSON object that conforms to this schema:\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```"
        )
        key = compute_seed_key(system=system, prompt=full_prompt, schema=schema)

        self.used_keys_this_pass.add(key)
        self.keys_by_stage.setdefault(self.current_scenario, {}).setdefault(
            effective_call, set()
        ).add(key)

        # 1. Existing seed cache hit
        if key in self.seed_data:
            entry = self.seed_data[key]
            if isinstance(entry, dict) and "response" in entry:
                return entry["response"]
            return entry

        # 2. Key is missing from seed cache: dry run discovery
        if self.dry_run:
            self.new_keys_this_pass += 1
            dummy = _get_dummy_response(effective_call)
            # Store in memory only for current pass verification; zero files written
            self.seed_data[key] = {
                "response": dummy,
                "metadata": {
                    "scenario": self.current_scenario,
                    "stage": effective_call,
                    "model": self._model,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "prompt_version": PROMPT_VERSION,
                },
            }
            return dummy

        # 3. Live call execution
        if self.total_live_calls >= self.max_calls:
            raise RuntimeError(
                f"Maximum call limit reached ({self.max_calls}); stopping recording."
            )

        if self.daily_quota_hit:
            raise RuntimeError(
                "Daily quota limit hit in previous call; skipping further live requests."
            )

        try:
            resp_data = await super()._chat(
                system=system,
                user=user,
                schema=schema,
                call_name=call_name,
            )
        except Exception as exc:
            err_msg = str(exc)
            if "RESOURCE_EXHAUSTED" in err_msg or "daily quota" in err_msg.lower():
                self.daily_quota_hit = True
                log.warning(
                    "gemini: Daily quota exhausted (429 RESOURCE_EXHAUSTED). Stopping cleanly."
                )
            raise

        # Safety check: NEVER store stub or fallback results
        if resp_data.get("actor") == "Stub" or getattr(self, "last_provider", "") == "stub":
            log.warning("Rejecting stub/fallback result from being stored in seed cache")
            self.pass_incomplete = True
            return resp_data

        # Save to seed data and persist atomically
        self.seed_data[key] = {
            "response": resp_data,
            "metadata": {
                "scenario": self.current_scenario,
                "stage": effective_call,
                "model": self._model,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "prompt_version": PROMPT_VERSION,
            },
        }
        self.new_keys_this_pass += 1
        self.live_calls_this_pass += 1
        self.total_live_calls += 1
        _save_seed_atomic(self.seed_file, self.seed_data, dry_run=self.dry_run)
        return resp_data


async def replay_scenario_for_recording(
    scenario_path: Path,
    client: RecordingGeminiClient,
    pass_num: int = 1,
    speed: float = 5.0,
    window_s: float = 2.0,
) -> None:
    scenario_name = scenario_path.stem
    client.start_scenario(scenario_name, pass_num=pass_num)

    engine = build_engine(
        provider="stub",
        kb_path=KB_PATH,
        attrib_window_s=window_s,
    )
    # Inject recording LLM
    engine.llm = client
    engine.attrib._llm = client
    engine.decide._llm = client

    # Warmup osint encoder to ensure deterministic grouping without timing race
    await engine.osint_cluster.warmup()

    tasks = start_engine_tasks(engine)
    stop_event = asyncio.Event()
    replay = ScenarioReplayService(
        engine.bus,
        scenario_path,
        speed=speed,
        max_delay_s=0.5,
        stop_when_done=stop_event,
    )

    replay_task = asyncio.create_task(replay.run())
    await stop_event.wait()
    await replay_task

    # Wait for pipeline idle: queue empty, no call in flight, 3s quiet
    quiet_s = 0.0
    for _ in range(900):  # max 180s
        await asyncio.sleep(0.2)
        if engine.attrib.is_idle:
            quiet_s += 0.2
            if quiet_s >= 3.0:
                break
        else:
            quiet_s = 0.0
    else:
        log.warning("Scenario %s did not reach pipeline idle within timeout", scenario_name)
        client.pass_incomplete = True

    # Check for fallbacks in engine services
    if (
        getattr(client, "fallback_count", 0) > 0
        or getattr(engine.attrib, "fallback_count", 0) > 0
        or getattr(engine.decide, "fallback_count", 0) > 0
    ):
        log.warning("Scenario %s experienced LLM fallback", scenario_name)
        client.pass_incomplete = True

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    engine.bus.close()


def print_summary(
    model: str,
    thinking_budget: int | None,
    timeout_s: float,
    dry_run: bool,
    pass_counts: list[int],
    total_entries_disk: int,
    keys_by_stage: dict[str, dict[str, set[str]]],
    seed_data: dict[str, Any],
    seed_file: Path,
    is_closed: bool,
) -> None:
    tb_str = f"{thinking_budget}" if thinking_budget is not None else "0 (disabled)"
    print("\n" + "=" * 76)
    print("           GEMINI OFFLINE SEED CACHE RECORDING SUMMARY")
    print("=" * 76)
    print(f"Model:                   {model}")
    print(f"Thinking Budget:         {tb_str}")
    print(f"Per-Call Timeout:        {timeout_s:.1f}s")
    if dry_run:
        print("Mode:                    DRY RUN (zero files written, no live API calls)")
        print(f"Seed Cache on Disk:      {seed_file} (Read-only, {total_entries_disk} entries)")
    else:
        print("Mode:                    LIVE API RECORDING")
        print(f"Seed Cache Target:       {seed_file} ({total_entries_disk} entries)")
    print(f"Passes Completed:        {len(pass_counts)}")
    pass_breakdown = ", ".join(f"Pass {i+1}: {cnt} new" for i, cnt in enumerate(pass_counts))
    print(f"New Keys Per Pass:       {pass_breakdown}")
    print("-" * 76)
    print(f"{'Scenario':<30} {'Stage':<20} {'Expected':<8} {'In Seed':<8} {'Missing':<8}")
    print("-" * 76)
    tot_exp = tot_rec = tot_mis = 0
    for scn in sorted(keys_by_stage.keys()):
        for stg in STAGES:
            keys = keys_by_stage[scn].get(stg, set())
            exp = len(keys)
            rec = sum(1 for k in keys if k in seed_data)
            mis = exp - rec
            tot_exp += exp
            tot_rec += rec
            tot_mis += mis
            print(f"{scn:<30} {stg:<20} {exp:<8} {rec:<8} {mis:<8}")
    print("-" * 76)
    print(f"{'TOTAL':<51} {tot_exp:<8} {tot_rec:<8} {tot_mis:<8}")
    print("=" * 76)
    if is_closed:
        print("STATUS: CLOSED (1 complete pass with 0 new keys and 0 live calls achieved)\n")
    else:
        print("STATUS: NOT CLOSED (Further passes or live calls required)\n")


async def run_recorder(
    scenarios: list[str],
    max_calls: int = 150,
    max_passes: int = 4,
    model: str = "gemini-3.1-flash-lite",
    seed_file: Path = DEFAULT_SEED_FILE,
    dry_run: bool = False,
    speed: float = 5.0,
    window_s: float = 2.0,
    transport: httpx.AsyncBaseTransport | None = None,
    prune: bool = False,
) -> tuple[int, list[int], dict[str, dict[str, set[str]]], bool]:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not dry_run and not transport:
        if not api_key:
            print("ERROR: GEMINI_API_KEY not found in environment or .env file.", file=sys.stderr)
            print("To discover expected calls without an API key, use --dry-run.", file=sys.stderr)
            sys.exit(1)

    timeout_s = float(os.environ.get("ORBIT_GEMINI_TIMEOUT_S", "30.0"))
    raw_tb = os.environ.get("ORBIT_GEMINI_THINKING_BUDGET")
    thinking_budget = int(raw_tb) if raw_tb else None

    # Resolve scenario paths
    scenario_paths: list[Path] = []
    for scn in scenarios:
        p = Path(scn)
        if not p.exists():
            p = REPO_ROOT / "scenarios" / (scn if scn.endswith(".jsonl") else f"{scn}.jsonl")
        if not p.exists():
            print(f"ERROR: Scenario file not found: {scn}", file=sys.stderr)
            sys.exit(1)
        scenario_paths.append(p)

    seed_data = _load_existing_seed(seed_file)
    kb = KB.load_from_json(KB_PATH)

    if dry_run:
        log.info("Dry-run mode: ZERO files will be written to disk. Inspecting seed from %s", seed_file)
    else:
        log.info("Live mode target seed file: %s (atomic swap via .tmp)", seed_file)

    client = RecordingGeminiClient(
        kb,
        seed_data,
        seed_file,
        model=model,
        api_key=api_key or "dry-run-redacted-key",
        dry_run=dry_run,
        max_calls=max_calls,
        transport=transport,
        timeout_s=timeout_s,
        thinking_budget=thinking_budget,
    )

    pass_counts: list[int] = []
    is_closed = False

    for pass_num in range(1, max_passes + 1):
        client.new_keys_this_pass = 0
        client.live_calls_this_pass = 0
        client.pass_incomplete = False
        client.used_keys_this_pass.clear()
        client.keys_by_stage.clear()
        log.info("Starting pass %d/%d across %d scenarios...", pass_num, max_passes, len(scenario_paths))

        for scn_path in scenario_paths:
            try:
                await replay_scenario_for_recording(
                    scn_path, client, pass_num=pass_num, speed=speed, window_s=window_s
                )
            except Exception as exc:
                log.warning("Exception during scenario %s: %s", scn_path.name, exc)
                client.pass_incomplete = True
                if client.daily_quota_hit or "RESOURCE_EXHAUSTED" in str(exc):
                    break

        pass_counts.append(client.new_keys_this_pass)
        log.info(
            "Pass %d completed: %d new keys added, %d live calls (incomplete=%s)",
            pass_num,
            client.new_keys_this_pass,
            client.live_calls_this_pass,
            client.pass_incomplete,
        )

        if client.daily_quota_hit:
            print("NOTICE: Daily quota reached. Stopping cleanly; seed file remains valid.")
            break

        # CLOSED means one complete pass with 0 new keys and 0 live calls
        if not client.pass_incomplete and client.new_keys_this_pass == 0 and client.live_calls_this_pass == 0:
            is_closed = True
            log.info("Pass %d is COMPLETE with 0 new keys and 0 live calls. Cache is CLOSED!", pass_num)
            break

    # If --prune is requested, delete seed entries not used in the final complete pass
    if prune and is_closed:
        stale_keys = [k for k in list(client.seed_data.keys()) if k not in client.used_keys_this_pass]
        for k in stale_keys:
            del client.seed_data[k]
        log.info("Pruned %d stale seed entries not used in final complete pass", len(stale_keys))
        if not dry_run:
            _save_seed_atomic(seed_file, client.seed_data)

    total_disk_entries = len(_load_existing_seed(seed_file)) if dry_run else len(client.seed_data)
    final_seed_for_summary = _load_existing_seed(seed_file) if dry_run else client.seed_data

    print_summary(
        model=model,
        thinking_budget=thinking_budget,
        timeout_s=timeout_s,
        dry_run=dry_run,
        pass_counts=pass_counts,
        total_entries_disk=total_disk_entries,
        keys_by_stage=client.keys_by_stage,
        seed_data=final_seed_for_summary,
        seed_file=seed_file,
        is_closed=is_closed,
    )

    return total_disk_entries, pass_counts, client.keys_by_stage, is_closed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record offline Gemini seed cache for demo scenarios."
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=DEFAULT_SCENARIOS,
        help="Scenarios to record (default: iran_counter_c5isr_brigade beat47 army_multidomain_attack_chain)",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=150,
        help="Safety ceiling on live API calls (default: 150)",
    )
    parser.add_argument(
        "--max-passes",
        type=int,
        default=4,
        help="Maximum recording passes to achieve cache closure (default: 4)",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.1-flash-lite",
        help="Gemini model to use (default: gemini-3.1-flash-lite)",
    )
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=DEFAULT_SEED_FILE,
        help="Target seed cache JSON file",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=5.0,
        help="Replay speed factor (default: 5.0)",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=2.0,
        help="Attribution sliding window in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate run without live Gemini calls to count required keys",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete seed entries not used in the final complete pass (stale window shapes)",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    _, _, _, is_closed = asyncio.run(
        run_recorder(
            scenarios=args.scenarios,
            max_calls=args.max_calls,
            max_passes=args.max_passes,
            model=args.model,
            seed_file=args.seed_file,
            dry_run=args.dry_run,
            speed=args.speed,
            window_s=args.window,
            prune=args.prune,
        )
    )

    if not is_closed:
        print("ERROR: Recording did not achieve cache closure.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
