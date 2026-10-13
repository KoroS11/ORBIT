"""Gemini-backed LLMClient (Google Generative Language REST API).

Reuses the prompt/parse plumbing of OllamaLLMClient and only swaps the HTTP
call. Attribution and decision output goes through the same validators the
Anthropic client uses.

Env:
  GEMINI_API_KEY (or GOOGLE_API_KEY)  required
  ORBIT_GEMINI_MODEL                  model id from your key's model list
  ORBIT_GEMINI_TIMEOUT_S              per-request timeout (default 60)
  ORBIT_LLM_FALLBACK=0                disable the stub fallback
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orbit.services.kb import KB
from orbit.services.kb.models import KBEntry
from orbit.services.llm.ollama_client import OllamaLLMClient, _extract_json
from orbit.services.llm.validation import (
    validate_and_repair_attribution,
    validate_and_repair_decision,
)
from orbit.services.schemas.events import (
    Anomaly,
    Attribution,
    AttributionChallenge,
    Decision,
)

log = logging.getLogger(__name__)

import time

# Default model that provides fast responses without 429 quota exhaustion.
DEFAULT_MODEL = "gemini-3.1-flash-lite"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
RETRY_STATUS = {429, 500, 503}
MAX_ATTEMPTS = 4


def _is_daily_quota_exhausted(resp: Any) -> bool:
    """Classify daily quota exhaustion strictly by quota ID in error.details.

    Matches PerDay vs PerMinute (case-insensitive, matching 'perday'/'perminute'
    with or without spaces/underscores/hyphens).
    If ambiguous (neither found, both found, or error.details missing), returns False
    so normal retries are preserved without tripping the circuit breaker.
    """
    try:
        data = resp.json() if hasattr(resp, "json") else {}
        if not isinstance(data, dict):
            return False
        err = data.get("error")
        if not isinstance(err, dict):
            return False
        details = err.get("details")
        if not isinstance(details, list) or not details:
            return False

        quota_tokens: list[str] = []
        for item in details:
            if isinstance(item, dict):
                # Inspect ErrorInfo metadata (e.g. quota_limit: 'GenerateContentRequestsPerDayPerProject')
                meta = item.get("metadata")
                if isinstance(meta, dict):
                    for v in meta.values():
                        if isinstance(v, str):
                            quota_tokens.append(v)
                # Inspect QuotaFailure violations (e.g. description: '... limit QueriesPerDay ...', quotaId)
                violations = item.get("violations")
                if isinstance(violations, list):
                    for v in violations:
                        if isinstance(v, dict):
                            desc = v.get("description")
                            if isinstance(desc, str):
                                quota_tokens.append(desc)
                            quota_id = v.get("quotaId") or v.get("quota_id")
                            if isinstance(quota_id, str):
                                quota_tokens.append(quota_id)
                # Direct quota ID fields in detail item
                for key in ("quotaId", "quota_id", "quotaLimit", "quota_limit"):
                    val = item.get(key)
                    if isinstance(val, str):
                        quota_tokens.append(val)
            elif isinstance(item, str):
                quota_tokens.append(item)

        if not quota_tokens:
            return False

        # Normalize tokens: lowercase, strip all spaces, underscores, hyphens
        normalized_str = re.sub(r"[\s_\-]+", "", " ".join(quota_tokens)).lower()

        has_per_day = "perday" in normalized_str
        has_per_minute = "perminute" in normalized_str

        # If daily quota is present and per-minute is NOT present -> daily exhaustion
        if has_per_day and not has_per_minute:
            return True
        # If per-minute is present, or both are present, or neither -> ambiguous or transient
        return False
    except Exception:
        return False


def _extract_quota_ids(details: list[Any]) -> list[str]:
    """Extract quota ID values and quota limits from Google error.details."""
    quota_ids: list[str] = []
    for item in details:
        if isinstance(item, dict):
            meta = item.get("metadata")
            if isinstance(meta, dict):
                for k, v in meta.items():
                    if "quota" in k.lower() or "limit" in k.lower():
                        quota_ids.append(f"{k}={v}")
            violations = item.get("violations")
            if isinstance(violations, list):
                for v in violations:
                    if isinstance(v, dict):
                        qid = v.get("quotaId") or v.get("quota_id")
                        if qid:
                            quota_ids.append(str(qid))
                        desc = v.get("description")
                        if desc:
                            quota_ids.append(str(desc))
            for k in ("quotaId", "quota_id", "quotaLimit", "quota_limit"):
                if k in item and isinstance(item[k], str):
                    quota_ids.append(item[k])
    return quota_ids


class GeminiCircuitBreaker:
    """Circuit breaker for Gemini daily quota exhaustion (RESOURCE_EXHAUSTED)."""

    def __init__(self, cooldown_minutes: float | None = None) -> None:
        self._cooldown_minutes = cooldown_minutes
        self._tripped_until: float = 0.0
        self._was_tripped: bool = False

    @property
    def cooldown_s(self) -> float:
        if self._cooldown_minutes is not None:
            return self._cooldown_minutes * 60.0
        raw = os.environ.get("ORBIT_GEMINI_CIRCUIT_BREAKER_MINUTES")
        try:
            minutes = float(raw) if raw is not None and raw.strip() != "" else 10.0
        except ValueError:
            minutes = 10.0
        return minutes * 60.0

    def is_open(self) -> bool:
        now = time.monotonic()
        if now < self._tripped_until:
            return True
        if self._was_tripped:
            log.info("gemini: circuit breaker reset after cooldown; attempting live request")
            self._was_tripped = False
            self._tripped_until = 0.0
        return False

    def trip(self) -> None:
        cooldown = self.cooldown_s
        self._tripped_until = time.monotonic() + cooldown
        self._was_tripped = True
        log.warning(
            "gemini: daily quota exhausted (RESOURCE_EXHAUSTED); circuit breaker tripped for %.1f minutes",
            cooldown / 60.0,
        )

    def reset(self) -> None:
        self._tripped_until = 0.0
        self._was_tripped = False


PROMPT_VERSION = "v1"


def _get_cache_file() -> Path:
    state_dir = os.environ.get("ORBIT_STATE_DIR")
    if state_dir:
        p = Path(state_dir)
    else:
        p = Path(__file__).resolve().parents[3] / "data" / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p / "gemini_cache.json"


class GeminiPromptCache:
    """Persistent prompt-hash cache under ORBIT_STATE_DIR."""

    def __init__(self, cache_file: Path | None = None) -> None:
        self._path = cache_file if cache_file is not None else _get_cache_file()
        self._lock = threading.Lock()
        self._memory: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                content = self._path.read_text(encoding="utf-8")
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError(f"Cache root must be a dict, got {type(data).__name__}")
                return data
            except Exception as exc:
                corrupt_path = self._path.with_name(f"{self._path.name}.corrupt")
                try:
                    if self._path.exists():
                        os.replace(self._path, corrupt_path)
                except Exception as move_err:
                    log.warning(
                        "Failed to rename corrupt cache %s to %s: %s",
                        self._path,
                        corrupt_path,
                        move_err,
                    )
                log.warning(
                    "Corrupted Gemini cache at %s renamed to %s: %s (starting with empty cache)",
                    self._path,
                    corrupt_path,
                    exc,
                )
        return {}

    def _persist(self) -> None:
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_name(f"{self._path.name}.tmp.{uuid.uuid4().hex}")
            tmp_path.write_text(
                json.dumps(self._memory, indent=2, default=str), encoding="utf-8"
            )
            os.replace(tmp_path, self._path)
        except Exception as exc:
            log.warning("Failed to persist Gemini cache to %s: %s", self._path, exc)

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._memory.get(key)
        if entry is None:
            return None
        # Keep reading old entry format:
        # Old format: entry is directly the response object/dict
        # New format: entry is {"response": ..., "metadata": ...}
        if isinstance(entry, dict) and "response" in entry and "metadata" in entry:
            return entry["response"]
        return entry

    def get_entry(self, key: str) -> dict[str, Any] | None:
        """Return raw entry including metadata if present."""
        with self._lock:
            return self._memory.get(key)

    def set(
        self, key: str, value: Any, metadata: dict[str, Any] | None = None
    ) -> None:
        with self._lock:
            if metadata is not None:
                self._memory[key] = {"response": value, "metadata": metadata}
            else:
                self._memory[key] = value
            self._persist_locked()

    def compute_key(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float = 0.2,
        thinking_budget: int | None = None,
        schema: dict[str, Any] | None = None,
        prompt_version: str = PROMPT_VERSION,
    ) -> str:
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "temperature": temperature,
            "thinking_budget": thinking_budget,
            "schema": schema,
            "prompt_version": prompt_version,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_seed_cache_file() -> Path:
    env_path = os.environ.get("ORBIT_GEMINI_SEED_CACHE")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[3] / "data" / "seed" / "gemini_cache_seed.json"


def compute_seed_key(
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any] | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    """Compute MODEL-INDEPENDENT seed cache key: sha256(system + prompt + schema + PROMPT_VERSION)."""
    payload = {
        "system": system,
        "prompt": prompt,
        "schema": schema,
        "prompt_version": prompt_version,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GeminiSeedCache:
    """Read-only seed cache loaded from data/seed/gemini_cache_seed.json."""

    def __init__(self, seed_file: Path | None = None) -> None:
        self._path = seed_file if seed_file is not None else _get_seed_cache_file()
        self._memory: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                content = self._path.read_text(encoding="utf-8")
                data = json.loads(content)
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                log.warning("Failed to load Gemini seed cache from %s: %s", self._path, exc)
        return {}

    def get(self, key: str) -> Any | None:
        entry = self._memory.get(key)
        if entry is None:
            return None
        # Unpack if stored in standard format with metadata
        if isinstance(entry, dict) and "response" in entry and "metadata" in entry:
            return entry["response"]
        return entry

    def get_entry(self, key: str) -> dict[str, Any] | None:
        """Return raw seed entry including metadata."""
        return self._memory.get(key)

    def __len__(self) -> int:
        return len(self._memory)


class GeminiRateLimiter:
    """Sliding-window process-shared rate limiter for Gemini API calls.

    Tracks calls over a 60-second sliding window. When capacity (ORBIT_GEMINI_MAX_RPM)
    is reached, calls wait their turn rather than triggering 429 errors.
    If the required wait exceeds ORBIT_GEMINI_MAX_QUEUE_WAIT_S (default 20s),
    raises RuntimeError so FallbackLLMClient falls back to the stub.
    """

    def __init__(
        self,
        max_rpm: int | None = None,
        max_queue_wait_s: float | None = None,
        *,
        clock: Any | None = None,
        sleep_fn: Any | None = None,
    ) -> None:
        raw_rpm = max_rpm if max_rpm is not None else os.environ.get("ORBIT_GEMINI_MAX_RPM")
        self.max_rpm = int(raw_rpm) if raw_rpm else 12
        raw_wait = (
            max_queue_wait_s
            if max_queue_wait_s is not None
            else os.environ.get("ORBIT_GEMINI_MAX_QUEUE_WAIT_S")
        )
        self.max_queue_wait_s = float(raw_wait) if raw_wait else 20.0
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
        from collections import deque
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait for available capacity or raise if wait > max_queue_wait_s."""
        async with self._lock:
            now = self._clock()
            # Purge calls older than 60 seconds
            while self._calls and self._calls[0] <= now - 60.0:
                self._calls.popleft()

            if len(self._calls) < self.max_rpm:
                self._calls.append(now)
                return 0.0

            oldest = self._calls[0]
            wait_s = (oldest + 60.0) - now
            if wait_s > self.max_queue_wait_s:
                raise RuntimeError(
                    f"gemini: rate limit wait of {wait_s:.1f}s exceeds maximum queue wait of {self.max_queue_wait_s:.1f}s (max_rpm={self.max_rpm})"
                )

            log.info(
                "gemini: rate limiter queuing call for %.2fs (rpm limit %d reached)",
                wait_s,
                self.max_rpm,
            )
            await self._sleep(wait_s)
            now_after = self._clock()
            while self._calls and self._calls[0] <= now_after - 60.0:
                self._calls.popleft()
            self._calls.append(now_after)
            return wait_s

    def reset(self) -> None:
        self._calls.clear()


_PROCESS_RATE_LIMITER: GeminiRateLimiter | None = None


def get_process_rate_limiter() -> GeminiRateLimiter:
    global _PROCESS_RATE_LIMITER
    if _PROCESS_RATE_LIMITER is None:
        _PROCESS_RATE_LIMITER = GeminiRateLimiter()
    return _PROCESS_RATE_LIMITER


def _retry_delay(resp: Any, attempt: int) -> float:
    try:
        if hasattr(resp, "headers") and "retry-after" in resp.headers:
            return min(float(resp.headers["retry-after"]), 30.0)
    except (KeyError, ValueError):
        pass
    return float(2**attempt)


def _response_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"Gemini returned no candidates: {json.dumps(data)[:300]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise ValueError(
            f"empty Gemini response (finishReason={candidates[0].get('finishReason')})"
        )
    return text


def _repair_attribution(attr: Attribution) -> Attribution:
    fixed = validate_and_repair_attribution(attr.model_dump()).repaired
    keys = ("actor", "confidence", "evidence", "kb_citations")
    return attr.model_copy(update={k: fixed[k] for k in keys})


class GeminiLLMClient(OllamaLLMClient):
    def __init__(
        self,
        kb: KB,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        thinking_budget: int | None = None,
        transport: Any | None = None,
        cache: GeminiPromptCache | None | bool = None,
        seed_cache: GeminiSeedCache | None | bool = None,
        circuit_breaker: GeminiCircuitBreaker | None = None,
        rate_limiter: GeminiRateLimiter | None | bool = None,
    ) -> None:
        # Deliberately skip OllamaLLMClient.__init__ (it configures an Ollama URL).
        self._kb = kb
        self._model = model or os.environ.get("ORBIT_GEMINI_MODEL") or DEFAULT_MODEL
        self._api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        raw_timeout = (
            timeout_s
            if timeout_s is not None
            else os.environ.get("ORBIT_GEMINI_TIMEOUT_S")
        )
        self._timeout_s = float(raw_timeout) if raw_timeout else 60.0
        raw_budget = (
            thinking_budget
            if thinking_budget is not None
            else os.environ.get("ORBIT_GEMINI_THINKING_BUDGET")
        )
        self._thinking_budget = (
            int(raw_budget)
            if raw_budget is not None and str(raw_budget).strip() != ""
            else None
        )
        self._transport = transport
        if cache is False:
            self._cache: GeminiPromptCache | None = None
        elif cache is not None:
            self._cache = cache
        else:
            self._cache = GeminiPromptCache()

        if seed_cache is False:
            self._seed_cache: GeminiSeedCache | None = None
        elif seed_cache is not None:
            self._seed_cache = seed_cache
        else:
            self._seed_cache = GeminiSeedCache()

        self._circuit_breaker = (
            circuit_breaker if circuit_breaker is not None else GeminiCircuitBreaker()
        )
        if rate_limiter is False:
            self._rate_limiter: GeminiRateLimiter | None = None
        elif rate_limiter is not None:
            self._rate_limiter = rate_limiter
        else:
            self._rate_limiter = get_process_rate_limiter()

        self.last_provider: str = "gemini"
        self._current_call_name: str = "chat"
        self.cache_hits: int = 0
        self.seed_hits: int = 0
        self.live_calls: int = 0
        log.info(
            "gemini: model=%s timeout=%.0fs thinking_budget=%s",
            self._model,
            self._timeout_s,
            self._thinking_budget,
        )

    @property
    def stub_fallbacks(self) -> int:
        return 0

    async def _chat(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        call_name: str | None = None,
    ) -> dict:
        import httpx

        effective_call = call_name or getattr(self, "_current_call_name", "chat")

        prompt = (
            f"{user}\n\n"
            "Reply with a single JSON object that conforms to this schema:\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```"
        )
        # 1. State cache lookup (model-dependent key)
        cache_key = None
        if self._cache is not None:
            cache_key = self._cache.compute_key(
                model=self._model,
                system=system,
                prompt=prompt,
                temperature=0.2,
                thinking_budget=self._thinking_budget,
                schema=schema,
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.info("gemini: cache hit for key %s (call=%s)", cache_key[:12], effective_call)
                self.last_provider = "gemini-cache"
                self.cache_hits += 1
                return cached

        # 2. Seed cache lookup (model-independent key)
        if self._seed_cache is not None:
            seed_key = compute_seed_key(
                system=system,
                prompt=prompt,
                schema=schema,
            )
            seed_cached = self._seed_cache.get(seed_key)
            if seed_cached is not None:
                log.info("gemini: seed cache hit for key %s (call=%s)", seed_key[:12], effective_call)
                self.last_provider = "gemini-seed"
                self.seed_hits += 1
                return seed_cached

        # 3. Circuit breaker check
        if self._circuit_breaker.is_open():
            log.warning("gemini: circuit breaker is open; skipping API call (call=%s)", effective_call)
            raise RuntimeError("gemini: circuit breaker open (RESOURCE_EXHAUSTED)")

        # 4. Client-side rate limiter check: wait turn or raise if wait > max_queue_wait_s
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()

        gen_config: dict[str, Any] = {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        }
        if self._thinking_budget is not None:
            gen_config["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        url = f"{API_ROOT}/models/{self._model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,  # header, so it never lands in URLs/logs
            "x-orbit-call": effective_call,
        }
        client_kwargs: dict[str, Any] = {"timeout": self._timeout_s}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                t0 = time.monotonic()
                try:
                    resp = await client.post(url, json=body, headers=headers)
                except httpx.TimeoutException as exc:
                    elapsed = time.monotonic() - t0
                    log.warning(
                        "gemini: timeout occurred (call=%s status=TIMEOUT error.status=None quota_ids=None elapsed=%.2fs exception=%s)",
                        effective_call,
                        elapsed,
                        type(exc).__name__,
                    )
                    raise

                elapsed = time.monotonic() - t0

                if resp.status_code == 429:
                    data = resp.json() if hasattr(resp, "json") else {}
                    err = data.get("error", {}) if isinstance(data, dict) else {}
                    if isinstance(err, dict):
                        err_status = err.get("status")
                        details = err.get("details", []) if isinstance(err.get("details"), list) else []
                    else:
                        err_status = str(err) if err else None
                        details = []
                    quota_ids = _extract_quota_ids(details)
                    log.warning(
                        "gemini: 429 received (call=%s status=%s error.status=%s quota_ids=%s elapsed=%.2fs)",
                        effective_call,
                        resp.status_code,
                        err_status,
                        quota_ids,
                        elapsed,
                    )
                    if _is_daily_quota_exhausted(resp):
                        self._circuit_breaker.trip()
                        resp.raise_for_status()

                if resp.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                    delay = _retry_delay(resp, attempt)
                    log.warning(
                        "gemini: HTTP %s, retry %d in %.0fs (call=%s)",
                        resp.status_code,
                        attempt,
                        delay,
                        effective_call,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                parsed = _extract_json(_response_text(resp.json()))
                if self._cache is not None:
                    if cache_key is None:
                        cache_key = self._cache.compute_key(
                            model=self._model,
                            system=system,
                            prompt=prompt,
                            temperature=0.2,
                            thinking_budget=self._thinking_budget,
                            schema=schema,
                        )
                    meta = {
                        "stage": effective_call,
                        "model": self._model,
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "prompt_version": PROMPT_VERSION,
                    }
                    self._cache.set(cache_key, parsed, metadata=meta)
                self.last_provider = "gemini"
                self.live_calls += 1
                log.info(
                    "gemini: call succeeded (call=%s model=%s attempt=%d elapsed=%.2fs)",
                    effective_call,
                    self._model,
                    attempt,
                    elapsed,
                )
                return parsed
        raise RuntimeError("gemini: retries exhausted")

    async def attribute(
        self, anomalies: list[Anomaly], kb_context: Iterable[KBEntry] = ()
    ) -> Attribution:
        self._current_call_name = "attribute"
        return await super().attribute(anomalies, kb_context)

    async def attribute_primary(
        self, anomalies: list[Anomaly], kb_context: Iterable[KBEntry] = ()
    ) -> Attribution:
        self._current_call_name = "attribute_primary"
        return _repair_attribution(await super().attribute_primary(anomalies, kb_context))

    async def attribute_redteam(
        self,
        primary: Attribution,
        anomalies: list[Anomaly],
        kb_context: Iterable[KBEntry] = (),
    ) -> AttributionChallenge:
        self._current_call_name = "attribute_redteam"
        return await super().attribute_redteam(primary, anomalies, kb_context)

    async def reconcile(
        self,
        primary: Attribution,
        challenge: AttributionChallenge,
        anomalies: list[Anomaly],
        kb_context: Iterable[KBEntry] = (),
    ) -> Attribution:
        self._current_call_name = "reconcile"
        return _repair_attribution(
            await super().reconcile(primary, challenge, anomalies, kb_context)
        )

    async def decide(self, attribution: Attribution) -> Decision:
        self._current_call_name = "decide"
        decision = await super().decide(attribution)
        fixed = validate_and_repair_decision(decision.model_dump())
        return decision.model_copy(update={"request_packet": fixed.get("request_packet")})


class FallbackLLMClient:
    """Use the primary client; on any failure use the fallback so the demo never stalls."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self._primary = primary
        self._fallback = fallback
        self.last_provider: str = getattr(primary, "last_provider", "gemini")
        self.fallback_count: int = 0
        self.last_fallback_at: str | None = None

    @property
    def cache_hits(self) -> int:
        return getattr(self._primary, "cache_hits", 0)

    @property
    def seed_hits(self) -> int:
        return getattr(self._primary, "seed_hits", 0)

    @property
    def live_calls(self) -> int:
        return getattr(self._primary, "live_calls", 0)

    @property
    def stub_fallbacks(self) -> int:
        return self.fallback_count

    @property
    def current_provider(self) -> str:
        return self.last_provider

    async def _call(self, name: str, *args: Any) -> Any:
        try:
            result = await getattr(self._primary, name)(*args)
            self.last_provider = getattr(self._primary, "last_provider", "gemini")
            return result
        except Exception as exc:  # noqa: BLE001 - CancelledError is not an Exception
            log.warning(
                "llm: %s failed (%s: %s); using fallback",
                name,
                type(exc).__name__,
                exc,
            )
            self.fallback_count += 1
            from datetime import datetime, timezone

            self.last_fallback_at = datetime.now(timezone.utc).isoformat()
            self.last_provider = "stub"
            return await getattr(self._fallback, name)(*args)

    async def attribute(self, anomalies, kb_context=()):
        return await self._call("attribute", anomalies, kb_context)

    async def attribute_primary(self, anomalies, kb_context=()):
        return await self._call("attribute_primary", anomalies, kb_context)

    async def attribute_redteam(self, primary, anomalies, kb_context=()):
        return await self._call("attribute_redteam", primary, anomalies, kb_context)

    async def reconcile(self, primary, challenge, anomalies, kb_context=()):
        return await self._call("reconcile", primary, challenge, anomalies, kb_context)

    async def decide(self, attribution):
        return await self._call("decide", attribution)
