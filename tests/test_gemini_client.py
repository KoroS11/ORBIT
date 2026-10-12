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
from orbit.services.llm.gemini_client import FallbackLLMClient, GeminiLLMClient
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
    )

    attr = await primary.attribute([_anomaly()])
    assert attr.actor == "China"
    assert call_count == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0
    assert breaker.is_open() is False


async def test_gemini_call_succeeded_log_emitted(caplog) -> None:
    """Successful Gemini call logs INFO message with model and attempt."""
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
    )

    with caplog.at_level(logging.INFO):
        await client.attribute([_anomaly()])

    assert any(
        "gemini: call succeeded (model=gemini-3.1-flash-lite attempt=1)" in record.message
        for record in caplog.records
    )



