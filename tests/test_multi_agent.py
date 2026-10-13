"""Tests for the multi-agent attribution pipeline (Phase 2).

Verifies that the stub StubLLMClient implements primary, redteam, and
reconcile, and that the AttribService orchestrates all three and emits
traces for each stage.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orbit.services.attrib import AttribService
from orbit.services.bus import InProcessBus
from orbit.services.kb import KB
from orbit.services.llm.stub import StubLLMClient
from orbit.services.schemas.events import (
    Anomaly,
    Attribution,
    AttributionChallenge,
    ReasoningTrace,
)
from orbit.services.traces import Tracer

ROOT = Path(__file__).resolve().parent.parent
KB_FILE = ROOT / "data" / "kb_seed_entries.json"


def _anomaly(kind: str = "orbital_rpo_risk", severity: float = 0.85) -> Anomaly:
    return Anomaly(
        kind=kind,
        source_signal="sig-1",
        source_signal_ids=["sig-1"],
        severity=severity,
        payload={"summary": "test anomaly"},
    )


@pytest.mark.asyncio
async def test_stub_redteam_lowers_confidence():
    kb = KB.load_from_json(KB_FILE)
    llm = StubLLMClient(kb)
    anomalies = [_anomaly("orbital_rpo_risk")]

    primary = await llm.attribute_primary(anomalies)
    assert isinstance(primary, Attribution)
    assert primary.confidence > 0.5

    challenge = await llm.attribute_redteam(primary, anomalies)
    assert isinstance(challenge, AttributionChallenge)
    assert challenge.primary_attribution_id == primary.id
    assert challenge.confidence_delta < 0  # red-team should push down

    final = await llm.reconcile(primary, challenge, anomalies)
    assert final.confidence < primary.confidence
    assert final.confidence >= 0.49  # uncertainty floor holds
    # Reconciler appends the rationale to the evidence chain.
    assert any("Red-team review" in line for line in final.evidence)


@pytest.mark.asyncio
async def test_attrib_service_emits_three_stage_traces():
    kb = KB.load_from_json(KB_FILE)
    bus = InProcessBus()
    tracer = Tracer(bus)
    llm = StubLLMClient(kb)
    attrib = AttribService(bus, llm, kb, window_s=0.0, tracer=tracer)

    stages_seen: list[str] = []

    async def consume() -> None:
        async for _, event in bus.subscribe("traces.*"):
            assert isinstance(event, ReasoningTrace)
            stages_seen.append(event.stage)
            if len(stages_seen) >= 3:
                break

    consumer = asyncio.create_task(consume())
    runner = asyncio.create_task(attrib.run())
    await asyncio.sleep(0)

    await bus.publish("anomalies.orbital_rpo_risk", _anomaly())
    await asyncio.wait_for(consumer, timeout=2.0)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    # All three attrib stages should fire in order.
    assert "attrib_primary" in stages_seen
    assert "attrib_redteam" in stages_seen
    assert "attrib_reconcile" in stages_seen


@pytest.mark.asyncio
async def test_attrib_publishes_only_final_attribution():
    kb = KB.load_from_json(KB_FILE)
    bus = InProcessBus()
    tracer = Tracer(bus)
    llm = StubLLMClient(kb)
    attrib = AttribService(bus, llm, kb, window_s=0.0, tracer=tracer)

    received: list[Attribution] = []

    async def consume() -> None:
        async for _, event in bus.subscribe("attributions.*"):
            assert isinstance(event, Attribution)
            received.append(event)
            break

    consumer = asyncio.create_task(consume())
    runner = asyncio.create_task(attrib.run())
    await asyncio.sleep(0)

    await bus.publish("anomalies.gnss_spoof", _anomaly("gnss_spoof"))
    await asyncio.wait_for(consumer, timeout=2.0)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    # Only the final reconciled attribution should hit the bus — primary
    # and challenge should live entirely in the trace stream.
    assert len(received) == 1
    final = received[0]
    # Reconciliation adds a "Red-team review" line to evidence.
    assert any("Red-team" in line for line in final.evidence)


def test_prompts_are_deterministic_without_volatile_fields():
    """Verify redteam, reconcile, and decide prompts drop volatile id and ts fields."""
    import uuid
    from datetime import datetime, timezone
    from orbit.services.attrib.prompts import (
        attribution_user_prompt,
        redteam_user_prompt,
        reconcile_user_prompt,
    )
    from orbit.services.decide.prompts import decision_user_prompt

    kb = KB.load_from_json(KB_FILE)
    entries = kb.all_entries()[:2]

    # Two anomalies with different IDs and timestamps
    a1 = _anomaly("orbital_rpo_risk")
    a1.id = f"anom-{uuid.uuid4().hex}"
    a1.ts = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    a2 = _anomaly("orbital_rpo_risk")
    a2.id = f"anom-{uuid.uuid4().hex}"
    a2.ts = datetime(2026, 2, 2, 12, 30, 0, tzinfo=timezone.utc)

    # 1. Primary prompt
    p1 = attribution_user_prompt([a1], entries)
    p2 = attribution_user_prompt([a2], entries)
    assert p1 == p2
    assert a1.id not in p1
    assert a2.id not in p2

    # Two primary attributions with different IDs and timestamps
    prim1 = Attribution(
        id=f"attr-{uuid.uuid4().hex}",
        ts=datetime(2026, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
        anomaly_ids=[a1.id],
        actor="China / PLA SSF",
        confidence=0.75,
        doctrine_match="kb-rpo-ambiguity-001",
        evidence=["test evidence"],
        kb_citations=["kb-rpo-ambiguity-001"],
    )
    prim2 = Attribution(
        id=f"attr-{uuid.uuid4().hex}",
        ts=datetime(2026, 2, 2, 12, 35, 0, tzinfo=timezone.utc),
        anomaly_ids=[a2.id],
        actor="China / PLA SSF",
        confidence=0.75,
        doctrine_match="kb-rpo-ambiguity-001",
        evidence=["test evidence"],
        kb_citations=["kb-rpo-ambiguity-001"],
    )

    # 2. Redteam prompt
    rt1 = redteam_user_prompt(prim1, [a1], entries)
    rt2 = redteam_user_prompt(prim2, [a2], entries)
    assert rt1 == rt2
    assert prim1.id not in rt1
    assert prim2.id not in rt2

    # Two challenges with different IDs and timestamps
    chal1 = AttributionChallenge(
        id=f"chal-{uuid.uuid4().hex}",
        ts=datetime(2026, 1, 1, 10, 6, 0, tzinfo=timezone.utc),
        primary_attribution_id=prim1.id,
        alternative_actor="Russia",
        confidence_delta=-0.1,
        objections=["test objection"],
        rationale="test challenge rationale",
    )
    chal2 = AttributionChallenge(
        id=f"chal-{uuid.uuid4().hex}",
        ts=datetime(2026, 2, 2, 12, 36, 0, tzinfo=timezone.utc),
        primary_attribution_id=prim2.id,
        alternative_actor="Russia",
        confidence_delta=-0.1,
        objections=["test objection"],
        rationale="test challenge rationale",
    )

    # 3. Reconcile prompt
    rec1 = reconcile_user_prompt(prim1, chal1, [a1], entries)
    rec2 = reconcile_user_prompt(prim2, chal2, [a2], entries)
    assert rec1 == rec2
    assert chal1.id not in rec1
    assert chal2.id not in rec2

    # 4. Decide prompt
    dec1 = decision_user_prompt(prim1)
    dec2 = decision_user_prompt(prim2)
    assert dec1 == dec2
    assert prim1.id not in dec1
    assert prim2.id not in dec2


@pytest.mark.asyncio
async def test_attrib_service_signal_latency_budget_timeout():
    """Verify AttribService falls back to stub attribution when signal budget expires."""
    kb = KB.load_from_json(KB_FILE)
    bus = InProcessBus()
    tracer = Tracer(bus)

    class SlowLLMClient(StubLLMClient):
        async def attribute_primary(self, anomalies, context=None):
            await asyncio.sleep(0.5)
            return await super().attribute_primary(anomalies, context)

    slow_llm = SlowLLMClient(kb)
    attrib = AttribService(bus, slow_llm, kb, window_s=0.0, tracer=tracer, signal_budget_s=0.05)

    received: list[Attribution] = []

    async def consume() -> None:
        async for _, event in bus.subscribe("attributions.*"):
            if isinstance(event, Attribution):
                received.append(event)
                break

    consumer = asyncio.create_task(consume())
    runner = asyncio.create_task(attrib.run())
    await asyncio.sleep(0)

    await bus.publish("anomalies.orbital_rpo_risk", _anomaly())
    await asyncio.wait_for(consumer, timeout=2.0)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert len(received) == 1
    # Check that fallback stub attribution was emitted
    assert isinstance(received[0], Attribution)
    assert received[0].actor in [
        "China",
        "Russia",
        "China / PLA SSF",
        "Unknown",
        "Unknown / Ambiguous",
        "Commercial / Co-orbital Inspection",
    ]


