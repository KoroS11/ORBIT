"""Tests for GeminiLLMClient using httpx.MockTransport.

Pins the wire format, authorization header, retry logic, validation repair,
and fallback behavior when the Gemini API encounters persistent errors.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from orbit.services.kb import KB
from orbit.services.llm.gemini_client import (
    FallbackLLMClient,
    GeminiLLMClient,
    GeminiRateLimiter,
)
from orbit.services.llm.stub import StubLLMClient
from orbit.services.llm.validation import UNCERTAINTY_ANCHOR
from orbit.services.schemas.events import Anomaly, Attribution

KB_FILE = Path(__file__).resolve().parent.parent / "data" / "kb_seed_entries.json"
FAKE_KEY = "AQ.fake-test-key-12345"


def _kb() -> KB:
    return KB.load_from_json(KB_FILE)


def _anomaly() -> Anomaly:
    return Anomaly(
        kind="orbital_rpo_risk",
        source_signal="orbit-beat47-002",
        source_signal_ids=["orbit-beat47-002"],
        severity=0.82,
        payload={
            "satellite": "ORBIT-LEO-07",
            "asset": "UNKNOWN-RSO-441",
            "summary": "test anomaly",
            "observables": {"miss_distance_km": 8.6},
        },
    )


def _gemini_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(payload)}
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }


# ---- Test Cases -------------------------------------------------------------


async def test_gemini_valid_response_parses_and_runs_validators() -> None:
    """a. Valid Gemini response parses into Attribution with uncertainty anchor in citations."""
    canned_payload = {
        "actor": "China",
        "confidence": 0.78,
        "doctrine_match": "kb-rpo-ambiguity-001",
        "evidence": ["consistent with SJ-21 precedent"],
        "predicted_next": "watch for SATCOM compound effect",
        "kb_citations": ["kb-rpo-ambiguity-001"],  # Note: missing uncertainty anchor initially
    }

    class _MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_MockTransport(),
        live_enabled=True,
    )
    attribution = await client.attribute([_anomaly()])

    assert attribution.actor == "China"
    assert attribution.confidence == pytest.approx(0.78)
    assert UNCERTAINTY_ANCHOR in attribution.kb_citations
    assert "kb-rpo-ambiguity-001" in attribution.kb_citations


async def test_gemini_429_retry_then_succeeds(monkeypatch) -> None:
    """b. 429 followed by 200 retries and succeeds without delay."""
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    import orbit.services.llm.gemini_client as gemini_mod
    monkeypatch.setattr(gemini_mod.asyncio, "sleep", _fake_sleep)

    canned_payload = {
        "actor": "Russia",
        "confidence": 0.65,
        "evidence": ["ASAT testing signature"],
        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
    }

    attempts = 0

    class _RetryTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate limit"})
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_RetryTransport(),
        live_enabled=True,
    )
    attribution = await client.attribute([_anomaly()])

    assert attempts == 2
    assert len(sleep_calls) == 1
    assert attribution.actor == "Russia"


async def test_gemini_persistent_500_fallback_to_stub() -> None:
    """c. Persistent 500 makes FallbackLLMClient return StubLLMClient result."""
    class _Error500Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal server error"})

    import orbit.services.llm.gemini_client as gemini_mod
    # Instant sleep for any retries
    gemini_mod_sleep = gemini_mod.asyncio.sleep
    async def _noop_sleep(_: float) -> None:
        pass
    gemini_mod.asyncio.sleep = _noop_sleep

    try:
        kb = _kb()
        primary = GeminiLLMClient(
            kb,
            api_key=FAKE_KEY,
            model="gemini-2.5-flash",
            transport=_Error500Transport(),
            live_enabled=True,
        )
        stub = StubLLMClient(kb)
        fallback_client = FallbackLLMClient(primary, stub)

        attribution = await fallback_client.attribute([_anomaly()])
        # Stub result returns valid attribution for the anomaly
        assert isinstance(attribution, Attribution)
        assert attribution.actor is not None
        assert len(attribution.evidence) > 0
    finally:
        gemini_mod.asyncio.sleep = gemini_mod_sleep


async def test_gemini_key_in_header_not_in_url() -> None:
    """d. The request sends key in x-goog-api-key header and NOT in the URL."""
    canned_payload = {
        "actor": "Unknown",
        "confidence": 0.5,
        "evidence": ["test"],
        "kb_citations": [UNCERTAINTY_ANCHOR],
    }
    captured_requests: list[httpx.Request] = []

    class _CaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_CaptureTransport(),
        live_enabled=True,
    )
    await client.attribute([_anomaly()])

    assert len(captured_requests) == 1
    req = captured_requests[0]
    # Header check
    assert req.headers.get("x-goog-api-key") == FAKE_KEY
    # URL check: fake key must NOT appear in URL
    assert FAKE_KEY not in str(req.url)
    assert "key=" not in str(req.url)


async def test_gemini_thinking_budget_sent_when_set() -> None:
    """thinkingConfig is sent when ORBIT_GEMINI_THINKING_BUDGET is set."""
    canned_payload = {
        "actor": "Unknown",
        "confidence": 0.5,
        "evidence": ["test"],
        "kb_citations": [UNCERTAINTY_ANCHOR],
    }
    captured_requests: list[httpx.Request] = []

    class _CaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        thinking_budget=0,
        transport=_CaptureTransport(),
        live_enabled=True,
    )
    await client.attribute([_anomaly()])

    assert len(captured_requests) == 1
    req_body = json.loads(captured_requests[0].content)
    assert req_body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


async def test_gemini_thinking_budget_absent_when_unset(monkeypatch) -> None:
    """thinkingConfig is omitted when ORBIT_GEMINI_THINKING_BUDGET is unset."""
    monkeypatch.delenv("ORBIT_GEMINI_THINKING_BUDGET", raising=False)
    canned_payload = {
        "actor": "Unknown",
        "confidence": 0.5,
        "evidence": ["test"],
        "kb_citations": [UNCERTAINTY_ANCHOR],
    }
    captured_requests: list[httpx.Request] = []

    class _CaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_CaptureTransport(),
        live_enabled=True,
    )
    await client.attribute([_anomaly()])

    assert len(captured_requests) == 1
    req_body = json.loads(captured_requests[0].content)
    assert "thinkingConfig" not in req_body["generationConfig"]


async def test_gemini_prompt_cache_hit_and_miss(tmp_path: Path) -> None:
    """Cache stores response on miss and returns it on second call without network requests."""
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "test_cache.json"
    cache = GeminiPromptCache(cache_file=cache_file)

    network_calls = 0
    canned_payload = {
        "actor": "China",
        "confidence": 0.81,
        "evidence": ["satellite maneuver observed"],
        "kb_citations": ["kb-rpo-ambiguity-001", UNCERTAINTY_ANCHOR],
    }

    class _CountingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal network_calls
            network_calls += 1
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_CountingTransport(),
        cache=cache,
        live_enabled=True,
    )

    test_anomaly = _anomaly()

    # First call: cache miss, runs network request
    attr1 = await client.attribute([test_anomaly])
    assert network_calls == 1
    assert client.last_provider == "gemini"
    assert attr1.actor == "China"

    # Second call: identical prompt hits cache
    attr2 = await client.attribute([test_anomaly])
    assert network_calls == 1  # No additional network call!
    assert client.last_provider == "gemini-cache"
    assert attr2.actor == "China"


async def test_gemini_prompt_cache_disk_persistence(tmp_path: Path) -> None:
    """Cached entries persist across client instances."""
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "persisted_cache.json"
    cache1 = GeminiPromptCache(cache_file=cache_file)
    cache1.set("key-123", {"status": "persisted"})

    cache2 = GeminiPromptCache(cache_file=cache_file)
    assert cache2.get("key-123") == {"status": "persisted"}


async def test_gemini_provider_tags_in_fallback() -> None:
    """Fallback client reports 'gemini' on success and 'stub' on 429 exhaustion."""
    class _Error429Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "exhausted"})

    import orbit.services.llm.gemini_client as gemini_mod
    orig_sleep = gemini_mod.asyncio.sleep
    async def _noop(_: float) -> None: pass
    gemini_mod.asyncio.sleep = _noop

    try:
        kb = _kb()
        primary = GeminiLLMClient(
            kb,
            api_key=FAKE_KEY,
            model="gemini-2.5-flash",
            transport=_Error429Transport(),
            live_enabled=True,
        )
        stub = StubLLMClient(kb)
        fallback = FallbackLLMClient(primary, stub)

        attr = await fallback.attribute([_anomaly()])
        assert fallback.last_provider == "stub"
        assert fallback.current_provider == "stub"
        assert isinstance(attr, Attribution)
    finally:
        gemini_mod.asyncio.sleep = orig_sleep


def test_gemini_retry_delay_respects_retry_after() -> None:
    from orbit.services.llm.gemini_client import _retry_delay

    class MockResp:
        headers = {"retry-after": "5"}

    assert _retry_delay(MockResp(), 1) == 5.0

    class NoHeaderResp:
        headers = {}

    assert _retry_delay(NoHeaderResp(), 3) == 8.0


REAL_GOOGLE_DAILY_429 = {
    "error": {
        "code": 429,
        "message": "Resource has been exhausted (e.g. check quota).",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "subject": "project:1234567890",
                        "description": "Quota exceeded for quota metric 'Queries' and limit 'QueriesPerDay' of service 'generativelanguage.googleapis.com'.",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "RATE_LIMIT_EXCEEDED",
                "domain": "googleapis.com",
                "metadata": {
                    "consumer": "projects/1234567890",
                    "quota_limit": "GenerateContentRequestsPerDayPerProject",
                    "quota_metric": "generativelanguage.googleapis.com/generate_content_requests",
                    "service": "generativelanguage.googleapis.com",
                },
            },
        ],
    }
}

REAL_GOOGLE_PER_MINUTE_429 = {
    "error": {
        "code": 429,
        "message": "Resource has been exhausted (e.g. check quota).",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "subject": "project:1234567890",
                        "description": "Quota exceeded for quota metric 'Queries' and limit 'QueriesPerMinute' of service 'generativelanguage.googleapis.com'.",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "RATE_LIMIT_EXCEEDED",
                "domain": "googleapis.com",
                "metadata": {
                    "consumer": "projects/1234567890",
                    "quota_limit": "GenerateRequestsPerMinutePerProject",
                    "quota_metric": "generativelanguage.googleapis.com/generate_content_requests",
                    "service": "generativelanguage.googleapis.com",
                },
            },
        ],
    }
}


async def test_circuit_breaker_trips_on_daily_quota_and_skips_retries(monkeypatch) -> None:
    """Daily quota exhaustion (RESOURCE_EXHAUSTED with PerDay quota ID) trips breaker without retries."""
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    import orbit.services.llm.gemini_client as gemini_mod
    monkeypatch.setattr(gemini_mod.asyncio, "sleep", _fake_sleep)

    network_call_count = 0

    class _DailyExhaustionTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal network_call_count
            network_call_count += 1
            return httpx.Response(429, json=REAL_GOOGLE_DAILY_429)

    kb = _kb()
    breaker = gemini_mod.GeminiCircuitBreaker(cooldown_minutes=10)
    primary = GeminiLLMClient(
        kb,
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_DailyExhaustionTransport(),
        circuit_breaker=breaker,
        live_enabled=True,
    )
    stub = StubLLMClient(kb)
    fallback = FallbackLLMClient(primary, stub)

    assert fallback.fallback_count == 0
    assert fallback.last_fallback_at is None

    # First call: receives 429 with PerDay in error.details
    attr = await fallback.attribute([_anomaly()])
    assert isinstance(attr, Attribution)
    assert fallback.fallback_count == 1
    assert fallback.last_fallback_at is not None
    assert fallback.last_provider == "stub"
    assert network_call_count == 1
    # Crucial: 0 retries performed (no 2/4/8s sleep)
    assert sleep_calls == []
    assert breaker.is_open() is True

    # Second call while breaker is open: should NOT make any network call
    attr2 = await fallback.attribute([_anomaly()])
    assert isinstance(attr2, Attribution)
    assert fallback.fallback_count == 2
    assert network_call_count == 1  # Still 1, network was bypassed


async def test_circuit_breaker_resets_after_cooldown(monkeypatch) -> None:
    """Circuit breaker resets after cooldown period and allows live requests again."""
    import orbit.services.llm.gemini_client as gemini_mod

    breaker = gemini_mod.GeminiCircuitBreaker(cooldown_minutes=5)
    breaker.trip()
    assert breaker.is_open() is True

    # Advance fake monotonic time past 5 minutes (300 seconds)
    real_time = gemini_mod.time.monotonic()
    monkeypatch.setattr(gemini_mod.time, "monotonic", lambda: real_time + 301)

    assert breaker.is_open() is False


async def test_per_minute_429_keeps_backoff_and_does_not_trip_circuit_breaker(monkeypatch) -> None:
    """Per-minute 429 retains exponential backoff and succeeds on retry without tripping breaker."""
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    import orbit.services.llm.gemini_client as gemini_mod
    monkeypatch.setattr(gemini_mod.asyncio, "sleep", _fake_sleep)

    canned_payload = {
        "actor": "Russia",
        "confidence": 0.70,
        "evidence": ["rate-limit recovery"],
        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
    }

    call_count = 0

    class _PerMinuteRateLimitTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json=REAL_GOOGLE_PER_MINUTE_429)
            return httpx.Response(200, json=_gemini_response(canned_payload))

    kb = _kb()
    breaker = gemini_mod.GeminiCircuitBreaker(cooldown_minutes=10)
    primary = GeminiLLMClient(
        kb,
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_PerMinuteRateLimitTransport(),
        circuit_breaker=breaker,
        live_enabled=True,
    )

    attr = await primary.attribute([_anomaly()])
    assert attr.actor == "Russia"
    assert call_count == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0  # retry 1 in 2s
    assert breaker.is_open() is False


async def test_ambiguous_429_keeps_normal_retries_and_does_not_trip_circuit_breaker(monkeypatch) -> None:
    """Ambiguous 429 without clear PerDay quota ID keeps normal retries and does NOT trip breaker."""
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    import orbit.services.llm.gemini_client as gemini_mod
    monkeypatch.setattr(gemini_mod.asyncio, "sleep", _fake_sleep)

    canned_payload = {
        "actor": "China",
        "confidence": 0.75,
        "evidence": ["ambiguous recovered"],
        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
    }

    call_count = 0

    class _Ambiguous429Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Ambiguous: no details, generic error message
                return httpx.Response(
                    429,
                    json={
                        "error": {
                            "code": 429,
                            "message": "Too many requests",
                            "status": "RESOURCE_EXHAUSTED",
                        }
                    },
                )
            return httpx.Response(200, json=_gemini_response(canned_payload))

    kb = _kb()
    breaker = gemini_mod.GeminiCircuitBreaker(cooldown_minutes=10)
    primary = GeminiLLMClient(
        kb,
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_Ambiguous429Transport(),
        circuit_breaker=breaker,
        live_enabled=True,
    )

    attr = await primary.attribute([_anomaly()])
    assert attr.actor == "China"
    assert call_count == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0
    assert breaker.is_open() is False


async def test_gemini_call_succeeded_log_emitted(caplog) -> None:
    """Successful Gemini call logs INFO message with call name, model, attempt, and elapsed."""
    import logging

    canned_payload = {
        "actor": "China",
        "confidence": 0.81,
        "evidence": ["successful test"],
        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
    }

    class _SuccessTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_gemini_response(canned_payload))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_SuccessTransport(),
        live_enabled=True,
    )

    with caplog.at_level(logging.INFO):
        await client.attribute([_anomaly()])

    assert any(
        "gemini: call succeeded (call=attribute_primary model=gemini-3.1-flash-lite attempt=1 elapsed=" in record.message
        for record in caplog.records
    )


async def test_gemini_429_warning_log_emitted(caplog) -> None:
    """429 response logs WARNING with call name, status, error.status, quota IDs, and elapsed."""
    import logging
    from tests.test_gemini_client import REAL_GOOGLE_DAILY_429

    class _RateLimitedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json=REAL_GOOGLE_DAILY_429)

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_RateLimitedTransport(),
        live_enabled=True,
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(httpx.HTTPStatusError):
            await client.attribute_primary([_anomaly()])

    record = next(
        (r for r in caplog.records if "gemini: 429 received" in r.message),
        None,
    )
    assert record is not None
    assert record.levelno == logging.WARNING
    assert "call=attribute_primary" in record.message
    assert "status=429" in record.message
    assert "error.status=RESOURCE_EXHAUSTED" in record.message
    assert "GenerateContentRequestsPerDayPerProject" in record.message
    assert "elapsed=" in record.message


async def test_gemini_timeout_warning_log_emitted(caplog) -> None:
    """Timeout logs WARNING with call name, status=TIMEOUT, exception name, and elapsed."""
    import logging

    class _TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("The read operation timed out")

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-2.5-flash",
        transport=_TimeoutTransport(),
        live_enabled=True,
    )

    primary = Attribution(
        anomaly_ids=["sig-1"],
        actor="China",
        confidence=0.8,
        evidence=["test"],
        kb_citations=["kb-rpo-ambiguity-001"],
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(httpx.ReadTimeout):
            await client.attribute_redteam(primary, [_anomaly()])

    record = next(
        (r for r in caplog.records if "gemini: timeout occurred" in r.message),
        None,
    )
    assert record is not None
    assert record.levelno == logging.WARNING
    assert "call=attribute_redteam" in record.message
    assert "status=TIMEOUT" in record.message
    assert "error.status=None" in record.message
    assert "quota_ids=None" in record.message
    assert "exception=ReadTimeout" in record.message
    assert "elapsed=" in record.message


async def test_breaker_open_returns_cached_answer_with_gemini_cache_provider(tmp_path) -> None:
    """When circuit breaker is open, a cached prompt returns the cached answer with provider gemini-cache."""
    canned_payload = {
        "actor": "China",
        "confidence": 0.77,
        "evidence": ["cached evidence"],
        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
    }

    from orbit.services.llm.gemini_client import (
        FallbackLLMClient,
        GeminiCircuitBreaker,
        GeminiPromptCache,
    )
    from orbit.services.llm.stub import StubLLMClient

    cache_file = tmp_path / "gemini_cache.json"
    cache = GeminiPromptCache(cache_file=cache_file)
    breaker = GeminiCircuitBreaker(cooldown_minutes=10)

    call_count = 0

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("Transport should not be called when cached!")
            return httpx.Response(200, json=_gemini_response(canned_payload))

    primary = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_Transport(),
        circuit_breaker=breaker,
        cache=cache,
        live_enabled=True,
    )
    fallback = FallbackLLMClient(primary, StubLLMClient(_kb()))

    anomaly = _anomaly()

    # First call: live call succeeds, populates cache
    attr1 = await fallback.attribute_primary([anomaly])
    assert attr1.actor == "China"
    assert call_count == 1
    assert fallback.last_provider == "gemini"

    # Trip the circuit breaker
    breaker.trip()
    assert breaker.is_open() is True

    # Second call with breaker OPEN: cache lookup happens BEFORE breaker check,
    # so cached answer is returned with provider "gemini-cache" and zero transport calls!
    attr2 = await fallback.attribute_primary([anomaly])
    assert attr2.actor == "China"
    assert call_count == 1  # No network call
    assert primary.last_provider == "gemini-cache"
    assert fallback.last_provider == "gemini-cache"


def test_gemini_prompt_cache_atomic_write_and_lock(tmp_path: Path):
    """Cache persists atomically via temp file + replace and is thread-safe."""
    import concurrent.futures
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "cache.json"
    cache = GeminiPromptCache(cache_file=cache_file)

    def _writer(i: int):
        cache.set(f"key_{i}", {"val": i}, metadata={"idx": i})

    # Run 20 concurrent thread writes
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_writer, range(20)))

    # Verify all 20 entries persisted
    loaded = GeminiPromptCache(cache_file=cache_file)
    for i in range(20):
        assert loaded.get(f"key_{i}") == {"val": i}
        assert loaded.get_entry(f"key_{i}")["metadata"]["idx"] == i

    # Verify no dangling temp files remain in directory
    tmps = list(tmp_path.glob("*.tmp.*"))
    assert len(tmps) == 0


def test_gemini_prompt_cache_corrupt_file_recovery_and_warning(tmp_path: Path, caplog):
    """On load error, bad file is renamed to *.corrupt and a WARNING is logged."""
    import logging
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text("{invalid json truncated", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cache = GeminiPromptCache(cache_file=cache_file)

    # Bad file renamed to cache.json.corrupt
    corrupt_file = tmp_path / "cache.json.corrupt"
    assert corrupt_file.exists()
    assert corrupt_file.read_text(encoding="utf-8") == "{invalid json truncated"
    # Original file no longer exists or was moved
    assert not cache_file.exists()
    # Cache started with empty dict
    assert cache._memory == {}
    # WARNING log was emitted
    assert any("Corrupted Gemini cache at" in r.message for r in caplog.records)
    assert any("renamed to" in r.message for r in caplog.records)


def test_gemini_prompt_cache_non_dict_corrupt_file_recovery(tmp_path: Path, caplog):
    """If cache file contains JSON list or scalar instead of dict, rename to *.corrupt."""
    import logging
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text("[1, 2, 3]", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        cache = GeminiPromptCache(cache_file=cache_file)

    corrupt_file = tmp_path / "cache.json.corrupt"
    assert corrupt_file.exists()
    assert cache._memory == {}
    assert any("Corrupted Gemini cache at" in r.message for r in caplog.records)


def test_gemini_prompt_cache_prompt_version_in_key():
    """Cache key includes PROMPT_VERSION so bumps invalidate old keys."""
    from orbit.services.llm.gemini_client import PROMPT_VERSION, GeminiPromptCache

    cache = GeminiPromptCache(cache_file=Path("nonexistent.json"))
    k1 = cache.compute_key(
        model="gemini-3.1-flash-lite",
        system="sys",
        prompt="prompt",
        prompt_version="v1",
    )
    k2 = cache.compute_key(
        model="gemini-3.1-flash-lite",
        system="sys",
        prompt="prompt",
        prompt_version="v2",
    )
    assert k1 != k2
    # Default uses current PROMPT_VERSION constant
    k_default = cache.compute_key(
        model="gemini-3.1-flash-lite",
        system="sys",
        prompt="prompt",
    )
    assert k_default == k1 if PROMPT_VERSION == "v1" else k2


def test_gemini_prompt_cache_reads_old_and_new_entry_formats(tmp_path: Path):
    """Cache seamlessly reads old format (raw dict) and new format (with metadata)."""
    import json
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "cache.json"
    raw_data = {
        "old_entry_key": {
            "actor": "Russia",
            "confidence": 0.65,
            "evidence": ["legacy format"],
        },
        "new_entry_key": {
            "response": {
                "actor": "China",
                "confidence": 0.85,
                "evidence": ["new format"],
            },
            "metadata": {
                "stage": "attribute_primary",
                "model": "gemini-3.1-flash-lite",
                "recorded_at": "2026-10-13T12:00:00Z",
                "prompt_version": "v1",
            },
        },
    }
    cache_file.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")

    cache = GeminiPromptCache(cache_file=cache_file)

    # Both return the response payload
    old_res = cache.get("old_entry_key")
    assert old_res == {
        "actor": "Russia",
        "confidence": 0.65,
        "evidence": ["legacy format"],
    }

    new_res = cache.get("new_entry_key")
    assert new_res == {
        "actor": "China",
        "confidence": 0.85,
        "evidence": ["new format"],
    }

    # get_entry preserves full metadata for new format
    entry = cache.get_entry("new_entry_key")
    assert entry["metadata"]["stage"] == "attribute_primary"
    assert entry["metadata"]["prompt_version"] == "v1"


def test_gemini_seed_cache_model_independent_key():
    """Seed cache key is model-independent: sha256(system + prompt + schema + PROMPT_VERSION)."""
    from orbit.services.llm.gemini_client import PROMPT_VERSION, compute_seed_key

    k1 = compute_seed_key(
        system="system text",
        prompt="user prompt text",
        schema={"type": "object"},
        prompt_version="v1",
    )
    k2 = compute_seed_key(
        system="system text",
        prompt="user prompt text",
        schema={"type": "object"},
        prompt_version="v1",
    )
    assert k1 == k2

    # Different schema or prompt produces different key
    k3 = compute_seed_key(
        system="system text",
        prompt="different prompt text",
        schema={"type": "object"},
        prompt_version="v1",
    )
    assert k1 != k3


async def test_lookup_order_state_cache_seed_live_stub(tmp_path: Path):
    """Lookup order strictly follows: state cache -> seed -> live -> stub."""
    import json
    from orbit.services.llm.gemini_client import (
        FallbackLLMClient,
        GeminiCircuitBreaker,
        GeminiLLMClient,
        GeminiPromptCache,
        GeminiSeedCache,
        compute_seed_key,
    )
    from orbit.services.llm.stub import StubLLMClient

    # 1. Prepare isolated state cache and seed cache
    state_file = tmp_path / "state_cache.json"
    seed_file = tmp_path / "seed_cache.json"

    state_cache = GeminiPromptCache(cache_file=state_file)
    seed_data = {}
    seed_file.write_text(json.dumps(seed_data), encoding="utf-8")
    seed_cache = GeminiSeedCache(seed_file=seed_file)

    network_calls = 0

    class _MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal network_calls
            network_calls += 1
            return httpx.Response(200, json=_gemini_response({
                "actor": "China",
                "confidence": 0.8,
                "evidence": ["live"],
                "kb_citations": ["kb-rpo-ambiguity-001", UNCERTAINTY_ANCHOR],
            }))

    kb = _kb()
    primary = GeminiLLMClient(
        kb,
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_MockTransport(),
        cache=state_cache,
        seed_cache=seed_cache,
        live_enabled=True,
    )
    fallback = FallbackLLMClient(primary, StubLLMClient(kb))

    anomaly = _anomaly()

    # Step 1: Miss both state and seed -> Live call succeeds
    attr_live = await fallback.attribute([anomaly])
    assert attr_live.actor == "China"
    assert network_calls == 1
    assert fallback.live_calls == 1
    assert fallback.cache_hits == 0
    assert fallback.seed_hits == 0
    assert fallback.last_provider == "gemini"

    # Step 2: Second call with identical input hits State Cache
    attr_state = await fallback.attribute([anomaly])
    assert attr_state.actor == "China"
    assert network_calls == 1  # No additional network call
    assert fallback.cache_hits == 1
    assert fallback.seed_hits == 0
    assert fallback.last_provider == "gemini-cache"

    # Step 3: Clear state cache; populate seed cache with different prompt
    state_cache._memory.clear()
    state_cache._persist()

    # Craft an entry in seed cache using compute_seed_key
    from orbit.services.attrib.prompts import (
        ATTRIBUTION_TOOL,
        attribution_system_prompt,
        attribution_user_prompt,
    )
    system = attribution_system_prompt()
    schema = ATTRIBUTION_TOOL["input_schema"]
    user = attribution_user_prompt([anomaly], primary._resolve_kb_context([anomaly], ()))
    full_prompt = (
        f"{user}\n\n"
        "Reply with a single JSON object that conforms to this schema:\n"
        f"```json\n{json.dumps(schema, indent=2)}\n```"
    )
    s_key = compute_seed_key(system=system, prompt=full_prompt, schema=schema)
    seed_data[s_key] = {
        "response": {
            "actor": "Russia",
            "confidence": 0.72,
            "evidence": ["from seed"],
            "kb_citations": ["kb-rpo-ambiguity-001", UNCERTAINTY_ANCHOR],
        },
        "metadata": {
            "scenario": "beat47",
            "stage": "attribute_primary",
            "model": "gemini-3.1-flash-lite",
            "recorded_at": "2026-10-13T12:00:00Z",
            "prompt_version": "v1",
        },
    }
    seed_file.write_text(json.dumps(seed_data), encoding="utf-8")
    seed_cache._memory = seed_cache._load()

    attr_seed = await fallback.attribute([anomaly])
    assert attr_seed.actor == "Russia"
    assert network_calls == 1  # No network call, hit seed cache
    assert fallback.seed_hits == 1
    assert fallback.last_provider == "gemini-seed"

    # Step 4: Clear both state and seed, trip circuit breaker -> Falls back to Stub
    state_cache._memory.clear()
    seed_cache._memory.clear()
    primary._circuit_breaker.trip()

    attr_stub = await fallback.attribute([anomaly])
    assert fallback.stub_fallbacks == 1
    assert fallback.last_provider == "stub"
    assert isinstance(attr_stub, Attribution)


def test_seed_cache_file_safety_and_size():
    """Verify data/seed/gemini_cache_seed.json is < 5MB and contains no API keys."""
    import re
    from pathlib import Path

    seed_path = Path("data/seed/gemini_cache_seed.json")
    assert seed_path.exists(), "data/seed/gemini_cache_seed.json must exist"

    size_bytes = seed_path.stat().st_size
    max_size = 5 * 1024 * 1024  # 5 MB
    assert size_bytes < max_size, f"Seed cache size {size_bytes} exceeds 5 MB limit"

    content = seed_path.read_text(encoding="utf-8")
    # Scan for Google API key prefix (AIza...) or OpenAI/Anthropic secret keys
    api_key_patterns = [
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        re.compile(r"AIza[0-9A-Za-z\-_]+"),
        re.compile(r"sk-[0-9A-Za-z\-_]{20,}"),
    ]
    for pattern in api_key_patterns:
        matches = pattern.findall(content)
        assert len(matches) == 0, f"Detected API key-like strings in seed cache: {matches}"


def test_committed_seed_provenance_and_metadata():
    """Verify that committed data/seed/gemini_cache_seed.json contains no mock entries
    and that any present entry has valid recorded_at and model metadata."""
    from pathlib import Path

    seed_path = Path("data/seed/gemini_cache_seed.json")
    assert seed_path.exists(), "data/seed/gemini_cache_seed.json must exist"

    raw = seed_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, dict), "Seed cache root must be a JSON object"

    for key, entry in data.items():
        assert isinstance(entry, dict), f"Entry {key} must be a dict"
        assert "response" in entry, f"Entry {key} missing 'response'"
        assert "metadata" in entry, f"Entry {key} missing 'metadata'"
        meta = entry["metadata"]
        assert isinstance(meta, dict), f"Entry {key} metadata must be a dict"
        assert "model" in meta and meta["model"], f"Entry {key} missing model metadata"
        assert "recorded_at" in meta and meta["recorded_at"], f"Entry {key} missing recorded_at timestamp"
        # Must not be marked as mock or test fixture
        assert meta.get("mock") is not True, f"Entry {key} marked as mock"
        assert "mock" not in str(meta.get("model", "")).lower(), f"Entry {key} has mock model"
        assert meta.get("scenario") != "mock_scenario", f"Entry {key} has mock scenario"


@pytest.mark.asyncio
async def test_rate_limiter_immediate_capacity_and_queuing():
    """Verify 12 calls pass immediately and 13th waits when under max_queue_wait_s."""
    current_time = 100.0

    def mock_clock() -> float:
        return current_time

    async def mock_sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay

    limiter = GeminiRateLimiter(
        max_rpm=12,
        max_queue_wait_s=20.0,
        clock=mock_clock,
        sleep_fn=mock_sleep,
    )

    # 1st call at t=100.0
    wait = await limiter.acquire()
    assert wait == 0.0

    # Next 11 calls at t=110.0
    current_time = 110.0
    for _ in range(11):
        wait = await limiter.acquire()
        assert wait == 0.0

    assert len(limiter._calls) == 12

    # Advance clock to t=145.0 (45s elapsed since oldest call at t=100.0)
    current_time = 145.0

    # 13th call requires waiting (100.0 + 60.0) - 145.0 = 15.0s, which is <= 20.0s
    wait = await limiter.acquire()
    assert wait == pytest.approx(15.0)
    assert current_time == pytest.approx(160.0)
    assert len(limiter._calls) == 12


@pytest.mark.asyncio
async def test_rate_limiter_exceeds_max_queue_wait_raises():
    """Verify call raising RuntimeError when wait exceeds max_queue_wait_s."""
    current_time = 100.0

    def mock_clock() -> float:
        return current_time

    async def mock_sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay

    limiter = GeminiRateLimiter(
        max_rpm=12,
        max_queue_wait_s=20.0,
        clock=mock_clock,
        sleep_fn=mock_sleep,
    )

    for _ in range(12):
        await limiter.acquire()

    # Advance clock by only 5 seconds to t=105.0. Wait would be 60 - 5 = 55s > 20s.
    current_time = 105.0
    with pytest.raises(RuntimeError, match="exceeds maximum queue wait"):
        await limiter.acquire()


@pytest.mark.asyncio
async def test_rate_limiter_exceeded_falls_back_to_stub():
    """Verify FallbackLLMClient catches rate limiter RuntimeError and falls back to stub."""
    kb = _kb()
    anomaly = _anomaly()

    current_time = 100.0

    def mock_clock() -> float:
        return current_time

    async def mock_sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay

    limiter = GeminiRateLimiter(
        max_rpm=1,
        max_queue_wait_s=10.0,
        clock=mock_clock,
        sleep_fn=mock_sleep,
    )
    # Fill capacity
    await limiter.acquire()

    client = GeminiLLMClient(
        kb,
        api_key=FAKE_KEY,
        cache=False,
        seed_cache=False,
        rate_limiter=limiter,
        live_enabled=True,
    )
    fallback = FallbackLLMClient(client, StubLLMClient(kb))

    # Next call would wait 60s > 10s, raising RuntimeError and triggering fallback
    attr = await fallback.attribute([anomaly])
    assert isinstance(attr, Attribution)
    assert fallback.last_provider == "stub"
    assert fallback.stub_fallbacks == 1
    assert fallback.fallback_count == 1


# ---- Live Gate Tests (ORBIT_GEMINI_LIVE) -----------------------------------


def test_is_gemini_live_enabled_parsing(monkeypatch) -> None:
    """Env parsing helper recognizes 1/true/yes/on as True and anything else as False."""
    from orbit.services.llm.gemini_client import is_gemini_live_enabled

    for val in ["1", "true", "TRUE", "yes", "YES", "on", "ON", " 1 ", " true "]:
        assert is_gemini_live_enabled(val) is True

    for val in ["", "0", "false", "no", "off", "invalid", "2"]:
        assert is_gemini_live_enabled(val) is False

    monkeypatch.setenv("ORBIT_GEMINI_LIVE", "1")
    assert is_gemini_live_enabled() is True
    monkeypatch.setenv("ORBIT_GEMINI_LIVE", "0")
    assert is_gemini_live_enabled() is False
    monkeypatch.delenv("ORBIT_GEMINI_LIVE", raising=False)
    assert is_gemini_live_enabled() is False


async def test_gemini_live_disabled_with_cache_hit_returns_recorded_answer_zero_transport_calls(tmp_path: Path) -> None:
    """When live is disabled (OFF), a state cache hit returns the recorded answer with zero transport calls."""
    from orbit.services.llm.gemini_client import GeminiPromptCache

    cache_file = tmp_path / "cache.json"
    cache = GeminiPromptCache(cache_file=cache_file)
    canned = {
        "actor": "China",
        "confidence": 0.82,
        "evidence": ["from state cache"],
        "kb_citations": ["kb-rpo-ambiguity-001", UNCERTAINTY_ANCHOR],
    }

    call_count = 0

    class _ZeroTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise AssertionError("Transport must not be called on cache hit when live is disabled!")

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_ZeroTransport(),
        cache=cache,
        seed_cache=False,
        live_enabled=False,
    )

    user_str = "test_user_prompt"
    prompt_str = (
        f"{user_str}\n\n"
        "Reply with a single JSON object that conforms to this schema:\n"
        f"```json\n{json.dumps(Attribution.model_json_schema(), indent=2)}\n```"
    )
    key = cache.compute_key(
        model=client._model,
        system="test_system",
        prompt=prompt_str,
        temperature=0.2,
        thinking_budget=client._thinking_budget,
        schema=Attribution.model_json_schema(),
    )
    cache.set(key, canned)

    # Directly test _chat lookup
    resp = await client._chat(
        system="test_system",
        user=user_str,
        schema=Attribution.model_json_schema(),
        call_name="attribute_primary",
    )
    assert resp["actor"] == "China"
    assert client.cache_hits == 1
    assert client.live_calls == 0
    assert client.last_provider == "gemini-cache"
    assert call_count == 0


async def test_gemini_live_disabled_with_seed_hit_returns_recorded_answer_zero_transport_calls(tmp_path: Path) -> None:
    """When live is disabled (OFF), a seed cache hit returns recorded answer with zero transport calls."""
    from orbit.services.llm.gemini_client import GeminiSeedCache, compute_seed_key

    seed_file = tmp_path / "seed.json"
    canned = {
        "actor": "Russia",
        "confidence": 0.79,
        "evidence": ["from seed cache"],
        "kb_citations": ["kb-rpo-ambiguity-001", UNCERTAINTY_ANCHOR],
    }

    user_str = _anomaly().model_dump_json()
    full_prompt = (
        f"{user_str}\n\n"
        "Reply with a single JSON object that conforms to this schema:\n"
        f"```json\n{json.dumps(Attribution.model_json_schema(), indent=2)}\n```"
    )
    seed_key = compute_seed_key(
        system="test_system",
        prompt=full_prompt,
        schema=Attribution.model_json_schema(),
    )
    seed_file.write_text(json.dumps({seed_key: {"response": canned, "metadata": {}}}), encoding="utf-8")
    seed_cache = GeminiSeedCache(seed_file=seed_file)

    call_count = 0

    class _ZeroTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise AssertionError("Transport must not be called on seed hit when live is disabled!")

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_ZeroTransport(),
        cache=False,
        seed_cache=seed_cache,
        live_enabled=False,
    )

    resp = await client._chat(
        system="test_system",
        user=user_str,
        schema=Attribution.model_json_schema(),
        call_name="attribute_primary",
    )
    assert resp["actor"] == "Russia"
    assert client.seed_hits == 1
    assert client.live_calls == 0
    assert client.last_provider == "gemini-seed"
    assert call_count == 0


async def test_gemini_live_disabled_with_miss_gives_stub_zero_transport_calls() -> None:
    """When live is disabled (OFF) and cache misses, FallbackLLMClient answers with stub, 0 transport calls, and breaker is NOT tripped."""
    from orbit.services.llm.gemini_client import GeminiCircuitBreaker, GeminiLiveDisabled

    call_count = 0

    class _ZeroTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise AssertionError("Transport must not be called on miss when live is disabled!")

    breaker = GeminiCircuitBreaker(cooldown_minutes=10)
    kb = _kb()
    primary = GeminiLLMClient(
        kb,
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_ZeroTransport(),
        cache=False,
        seed_cache=False,
        circuit_breaker=breaker,
        live_enabled=False,
    )

    # 1. Direct call raises GeminiLiveDisabled without touching network or tripping breaker
    with pytest.raises(GeminiLiveDisabled):
        await primary.attribute_primary([_anomaly()])

    assert breaker.is_open() is False
    assert call_count == 0

    # 2. Wrapped in FallbackLLMClient: catches GeminiLiveDisabled, returns stub, counts as stub fallback
    stub = StubLLMClient(kb)
    fallback = FallbackLLMClient(primary, stub)

    attr = await fallback.attribute([_anomaly()])
    assert isinstance(attr, Attribution)
    assert fallback.last_provider == "stub"
    assert fallback.stub_fallbacks == 1
    assert fallback.live_calls == 0
    assert fallback.seed_hits == 0
    assert fallback.cache_hits == 0
    assert breaker.is_open() is False
    assert call_count == 0


async def test_gemini_live_enabled_on_keeps_live_behavior() -> None:
    """When live is enabled (ON), cache miss proceeds to transport and succeeds."""
    call_count = 0
    canned = {
        "actor": "China",
        "confidence": 0.85,
        "evidence": ["live call verified"],
        "kb_citations": ["kb-rpo-ambiguity-001", UNCERTAINTY_ANCHOR],
    }

    class _LiveTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_gemini_response(canned))

    client = GeminiLLMClient(
        _kb(),
        api_key=FAKE_KEY,
        model="gemini-3.1-flash-lite",
        transport=_LiveTransport(),
        cache=False,
        seed_cache=False,
        live_enabled=True,
    )

    attr = await client.attribute([_anomaly()])
    assert attr.actor == "China"
    assert call_count == 1
    assert client.live_calls == 1
    assert client.last_provider == "gemini"








