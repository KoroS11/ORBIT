#!/usr/bin/env python3
"""Generate a temporary mock-recorded seed under the new windowing for test_real_path_proof.cjs."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx
from scripts.record_gemini_cache import run_recorder
from tests.test_record_cache import MockRecordingTransport


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True, help="Path to temporary mock seed file")
    args = parser.parse_args()

    transport = MockRecordingTransport()
    scenarios = [
        "iran_counter_c5isr_brigade",
        "beat47",
        "army_multidomain_attack_chain",
    ]

    total_entries, pass_counts, keys_by_stage, is_closed = await run_recorder(
        scenarios=scenarios,
        max_calls=200,
        max_passes=3,
        seed_file=args.output,
        speed=5.0,
        window_s=2.0,
        transport=transport,
        prune=True,
    )

    print(f"MOCK_SEED_GENERATED: {total_entries} entries, closed={is_closed}")
    if not is_closed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
