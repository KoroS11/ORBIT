import asyncio
import json
import logging
import sys
from pathlib import Path
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from orbit.services.kb import KB
from orbit.services.llm.gemini_client import GeminiLLMClient, GeminiPromptCache, FallbackLLMClient
from orbit.services.schemas.events import Anomaly

async def run():
    print("Testing persistent cache hit across pipeline replays...")
    kb = KB.load_from_json(REPO_ROOT / "data" / "kb_seed_entries.json")

    cache_file = REPO_ROOT / "data" / "state" / "gemini_cache_test.json"
    if cache_file.exists():
        cache_file.unlink()

    cache = GeminiPromptCache(cache_file=cache_file)

    network_calls = 0

    canned_gemini = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "actor": "Russia",
                        "confidence": 0.85,
                        "evidence": ["High confidence RF jamming signature matched"],
                        "kb_citations": ["kb-rpo-ambiguity-001", "kb-attribution-uncertainty-001"],
                    })
                }]
            }
        }]
    }

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal network_calls
            network_calls += 1
            return httpx.Response(200, json=canned_gemini)

    primary = GeminiLLMClient(
        kb,
        api_key="mock-api-key",
        model="gemini-2.5-flash",
        transport=MockTransport(),
        cache=cache,
    )

    dummy_anomaly = Anomaly(
        id="anom-cache-test-01",
        kind="rf_anomaly",
        source_signal="sig-01",
        source_signal_ids=["sig-01"],
        severity=0.88,
        payload={"domain": "rf_ew", "summary": "persistent Ku jamming"},
    )

    print("\n--- Pipeline Replay 1 (Cold Start) ---")
    res1 = await primary.attribute_primary([dummy_anomaly], kb.all_entries())
    print(f"Replay 1 result: actor={res1.actor} confidence={res1.confidence}")
    print(f"Network calls made: {network_calls}")
    print(f"Last provider: {primary.last_provider}")
    assert network_calls == 1, f"Expected 1 network call, got {network_calls}"
    assert primary.last_provider == "gemini", f"Expected 'gemini', got {primary.last_provider}"
    assert cache_file.exists(), "Cache file was not persisted"

    print("\n--- Pipeline Replay 2 (Warm Cache Replay) ---")
    res2 = await primary.attribute_primary([dummy_anomaly], kb.all_entries())
    print(f"Replay 2 result: actor={res2.actor} confidence={res2.confidence}")
    print(f"Network calls made: {network_calls}")
    print(f"Last provider: {primary.last_provider}")
    assert network_calls == 1, f"Network call occurred on cached replay! Total calls: {network_calls}"
    assert primary.last_provider == "gemini-cache", f"Expected 'gemini-cache', got {primary.last_provider}"
    assert res1.actor == res2.actor
    assert res1.confidence == res2.confidence

    if cache_file.exists():
        cache_file.unlink()

    print("\nPASSED: Cache hit proven on the second replay through the real pipeline (0 additional network calls).")

if __name__ == "__main__":
    asyncio.run(run())
