"""Verify that attribution window composition is strictly independent of LLM latency.

Runs replay for all three demo scenarios:
- iran_counter_c5isr_brigade
- beat47
- army_multidomain_attack_chain

Across 3 runs each at 0s mock latency and 4s mock latency, asserting that
window compositions are 100% identical.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from orbit._engine import build_engine, start_engine_tasks
from orbit.services.scenario_replay import ScenarioReplayService
from orbit.services.schemas.events import Anomaly, Attribution, Decision, AttributionChallenge


class MockLatencyLLM:
    """Mock LLM client that delays by `latency_s` on each call and records batches."""

    def __init__(self, latency_s: float):
        self.latency_s = latency_s
        self.recorded_windows: list[list[str]] = []

    async def attribute_primary(self, anomalies: list[Anomaly], context=None) -> Attribution:
        self.recorded_windows.append([a.id for a in anomalies])
        if self.latency_s > 0:
            await asyncio.sleep(self.latency_s)
        return Attribution(
            id="mock-attr-id",
            ts="2026-10-14T00:00:00Z",
            anomaly_ids=[a.id for a in anomalies],
            actor="Russia",
            confidence=0.8,
            doctrine_match="mock-doctrine",
            evidence=["mock evidence"],
            predicted_next="mock predicted",
            kb_citations=[],
        )

    async def attribute_redteam(self, primary: Attribution, *args, **kwargs) -> AttributionChallenge:
        return AttributionChallenge(
            primary_attribution_id=primary.id,
            alternative_actor="Unknown",
            confidence_delta=-0.1,
            objections=[],
            rationale="mock",
        )

    async def reconcile(self, *args, **kwargs) -> Attribution:
        return Attribution(
            id="mock-reconcile-id",
            ts="2026-10-14T00:00:00Z",
            anomaly_ids=[],
            actor="Russia",
            confidence=0.75,
            doctrine_match="mock-doctrine",
            evidence=["mock evidence"],
            predicted_next="mock predicted",
            kb_citations=[],
        )

    async def decide(self, *args, **kwargs) -> Decision:
        return Decision(
            id="mock-decision-id",
            ts="2026-10-14T00:00:00Z",
            attribution_id="mock-attr-id",
            action="passive_defense",
            target="target-1",
            rationale="mock",
            authority="local",
        )


async def _run_scenario_windows(scenario_name: str, latency_s: float) -> list[list[str]]:
    engine = build_engine(provider="stub")
    mock_llm = MockLatencyLLM(latency_s=latency_s)
    engine.attrib._llm = mock_llm
    engine.decide._llm = mock_llm
    await engine.osint_cluster.warmup()
    tasks = start_engine_tasks(engine)

    scenario_path = REPO_ROOT / "scenarios" / f"{scenario_name}.jsonl"
    stop_event = asyncio.Event()
    replay = ScenarioReplayService(
        engine.bus,
        scenario_path,
        speed=10.0,
        max_delay_s=0.25,
        stop_when_done=stop_event,
    )
    replay_task = asyncio.create_task(replay.run())
    await stop_event.wait()
    await replay_task

    # Wait for pipeline idle: queue empty, no call in flight, 3s quiet
    quiet_s = 0.0
    for _ in range(600):
        await asyncio.sleep(0.2)
        if engine.attrib.is_idle:
            quiet_s += 0.2
            if quiet_s >= 3.0:
                break
        else:
            quiet_s = 0.0

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    engine.bus.close()
    return mock_llm.recorded_windows


@pytest.mark.parametrize(
    "scenario_name",
    [
        "beat47",
        "army_multidomain_attack_chain",
        "iran_counter_c5isr_brigade",
    ],
)
@pytest.mark.asyncio
async def test_window_composition_latency_independence(scenario_name: str):
    """Verify identical window compositions at 0s and 4s latency across 3 runs each."""
    runs_0s: list[list[list[str]]] = []
    runs_4s: list[list[list[str]]] = []

    # 3 runs at 0s latency
    for _ in range(3):
        w = await _run_scenario_windows(scenario_name, latency_s=0.0)
        runs_0s.append(w)

    # 3 runs at 4s latency
    for _ in range(3):
        w = await _run_scenario_windows(scenario_name, latency_s=4.0)
        runs_4s.append(w)

    # 1. All 0s runs must be self-identical
    assert runs_0s[0] == runs_0s[1] == runs_0s[2], f"{scenario_name}: run-to-run variance at 0s"

    # 2. All 4s runs must be self-identical
    assert runs_4s[0] == runs_4s[1] == runs_4s[2], f"{scenario_name}: run-to-run variance at 4s"

    # 3. 0s and 4s window compositions must be identical
    assert runs_0s[0] == runs_4s[0], (
        f"{scenario_name}: window composition discrepancy between 0s and 4s latency!\n"
        f"0s windows: {runs_0s[0]}\n"
        f"4s windows: {runs_4s[0]}"
    )
