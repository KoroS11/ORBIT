"""Tests for scripts/record_gemini_cache.py.

Verifies that the offline seed cache recorder:
1. Records prompts and responses into a seed JSON file with a mock transport.
2. Is resumable: re-running with an existing seed file makes zero new network calls.
3. Stops cleanly on daily-quota 429 (RESOURCE_EXHAUSTED) without corrupting the file.
4. Terminates when cache closure is achieved.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.record_gemini_cache import run_recorder


def _gemini_mock_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(payload)}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }


class MockRecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.call_count: int = 0
        self.fail_on_call = fail_on_call

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        if self.fail_on_call is not None and self.call_count >= self.fail_on_call:
            # Simulate Gemini API 429 daily quota error body
            err_body = {
                "error": {
                    "code": 429,
                    "message": "Quota exceeded for quota metric 'Generate Content API requests' and limit 'Generate Content API requests per day'",
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "subject": "project:1234567890",
                                    "description": "Quota limit 'Generate Content API requests per day' exceeded.",
                                    "quotaId": "GenerateContentRequestsPerDay",
                                }
                            ],
                        }
                    ],
                }
            }
            return httpx.Response(429, json=err_body)

        data = json.loads(request.content.decode("utf-8"))
        user_prompt = (
            data.get("contents", [{}])[0].get("parts", [{}])[0].get("text", "")
        )

        if "submit_challenge" in user_prompt or "alternative_actor" in user_prompt:
            canned = {
                "alternative_actor": "Unknown / Ambiguous",
                "confidence_delta": -0.15,
                "objections": ["Alternative doctrine hypotheses remain plausible."],
                "rationale": "High observable ambiguity across cross-domain sensors.",
            }
        elif "submit_decision" in user_prompt or "target" in user_prompt:
            canned = {
                "action": "active_defense_escort",
                "target": "ORBIT-LEO-07",
                "rationale": "Multi-domain pressure requires defensive posture adjustment.",
                "authority": "local",
                "confidence": 0.78,
                "request_packet": None,
            }
        else:
            canned = {
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
        return httpx.Response(200, json=_gemini_mock_response(canned))


@pytest.mark.asyncio
async def test_recorder_records_resumes_and_terminates(tmp_path: Path):
    """Verify recording records into seed file, resumes on next run with 0 calls, and terminates."""
    seed_file = tmp_path / "gemini_cache_seed.json"
    transport = MockRecordingTransport()

    # Pass 1: Record beat47 scenario
    total_entries, pass_counts, stage_stats = await run_recorder(
        scenarios=["beat47"],
        max_calls=50,
        max_passes=3,
        seed_file=seed_file,
        speed=5.0,
        window_s=2.0,
        transport=transport,
    )

    assert seed_file.exists()
    assert total_entries == 20
    assert transport.call_count == 20
    assert pass_counts == [20, 0]  # Pass 1 recorded 20, Pass 2 verified 0 new keys -> closed!

    # Verify saved file structure
    saved = json.loads(seed_file.read_text(encoding="utf-8"))
    assert len(saved) == 20
    sample_key = next(iter(saved))
    assert "response" in saved[sample_key]
    assert "metadata" in saved[sample_key]
    assert saved[sample_key]["metadata"]["scenario"] == "beat47"
    assert saved[sample_key]["metadata"]["prompt_version"] == "v1"

    # Pass 2: Re-run with another transport on the same seed file -> Resumes with 0 calls!
    transport2 = MockRecordingTransport()
    total_entries_2, pass_counts_2, _ = await run_recorder(
        scenarios=["beat47"],
        max_calls=50,
        max_passes=3,
        seed_file=seed_file,
        speed=5.0,
        window_s=2.0,
        transport=transport2,
    )

    assert total_entries_2 == 20
    assert transport2.call_count == 0  # Zero new network calls!
    assert pass_counts_2 == [0]       # Terminated in pass 1 because 0 new keys!


@pytest.mark.asyncio
async def test_recorder_stops_cleanly_on_daily_quota_429(tmp_path: Path):
    """Verify that encountered 429 RESOURCE_EXHAUSTED stops recording cleanly and leaves file valid."""
    seed_file = tmp_path / "gemini_cache_seed_quota.json"
    # Fail on call 3 with 429 RESOURCE_EXHAUSTED
    transport = MockRecordingTransport(fail_on_call=3)

    total_entries, pass_counts, _ = await run_recorder(
        scenarios=["beat47"],
        max_calls=50,
        max_passes=2,
        seed_file=seed_file,
        speed=5.0,
        window_s=2.0,
        transport=transport,
    )

    # 2 calls succeeded before 429
    assert seed_file.exists()
    saved = json.loads(seed_file.read_text(encoding="utf-8"))
    assert len(saved) == 2
    assert total_entries == 2


@pytest.mark.asyncio
async def test_recorder_dry_run_writes_nothing_to_disk(tmp_path: Path):
    """Verify that --dry-run writes absolutely nothing to disk (no seed file, no state cache)."""
    seed_file = tmp_path / "never_created_seed.json"
    transport = MockRecordingTransport()

    total_disk_entries, pass_counts, stage_stats = await run_recorder(
        scenarios=["beat47"],
        max_calls=50,
        max_passes=3,
        seed_file=seed_file,
        dry_run=True,
        speed=5.0,
        window_s=2.0,
        transport=transport,
    )

    # ZERO files written
    assert not seed_file.exists()
    assert list(tmp_path.iterdir()) == []
    assert total_disk_entries == 0

    # Closure simulation in memory: pass 1 discovers 20 missing, pass 2 verifies 0 new
    assert pass_counts == [20, 0]

    # Coverage stats unambiguous: expected=5, recorded=0, missing=5 per stage
    beat_stats = stage_stats["beat47"]
    assert beat_stats["attribute_primary"]["expected"] == 5
    assert beat_stats["attribute_primary"]["recorded"] == 0
    assert beat_stats["attribute_primary"]["missing"] == 5
