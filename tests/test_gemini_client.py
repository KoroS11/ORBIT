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

