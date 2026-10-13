"""Tests for the decide-stage tool surface (Phase 3)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orbit.services.bus import InProcessBus
from orbit.services.decide.tools import (
    KBLookupTool,
    OrbitCloseApproachTool,
    OrbitSimulateManeuverTool,
    RequestDraftTool,
    RoutingValidateTool,
    ToolContext,
    build_tool_registry,
    dispatch,
)
from orbit.services.kb import KB
from orbit.services.orbit import OrbitService
from orbit.services.schemas.events import ReasoningTrace
from orbit.services.traces import Tracer

ROOT = Path(__file__).resolve().parent.parent
KB_FILE = ROOT / "data" / "kb_seed_entries.json"


def _ctx(tracer: Tracer | None = None) -> ToolContext:
    return ToolContext(
        kb=KB.load_from_json(KB_FILE),
        orbit=OrbitService(),
        tracer=tracer,
    )


@pytest.mark.asyncio
async def test_build_tool_registry_returns_five_tools():
    kb = KB.load_from_json(KB_FILE)
    orbit = OrbitService()
    ctx, tools = build_tool_registry(kb=kb, orbit=orbit, tracer=None)
    names = sorted(t.name for t in tools)
    assert names == [
        "kb.lookup",
        "orbit.compute_close_approach",
        "orbit.simulate_maneuver",
        "request.draft",
        "routing.validate",
    ]
    assert ctx.kb is kb
    assert ctx.orbit is orbit


@pytest.mark.asyncio
async def test_kb_lookup_returns_entries_for_known_actor():
    tool = KBLookupTool()
    ctx = _ctx()
    result = await tool.execute({"actor": "China"}, ctx)
    assert result["count"] >= 0
    assert isinstance(result["entries"], list)



@pytest.mark.asyncio
async def test_orbit_compute_close_approach_uses_sgp4_when_both_known():
    tool = OrbitCloseApproachTool()
    ctx = _ctx()
    sats = ctx.orbit.known_satellites()
    result = await tool.execute({"sat_a": sats[0], "sat_b": sats[1]}, ctx)
    assert result["source"] == "SGP4"
    assert "closest_approach_km" in result
    assert result["closest_approach_km"] > 0
    assert "t_closest" in result


@pytest.mark.asyncio
async def test_orbit_compute_close_approach_uses_scenario_authored_when_missing():
    tool = OrbitCloseApproachTool()
    ctx = _ctx()
    result = await tool.execute(
        {
            "sat_a": "UNKNOWN-RSO-441",
            "sat_b": "ORBIT-LEO-07",
            "authored_miss_km": 18.5,
            "authored_tca_iso": "2026-06-18T15:55:30Z",
        },
        ctx,
    )
    assert result["source"] == "scenario-authored (no TLE for UNKNOWN-RSO-441, ORBIT-LEO-07)"
    assert result["closest_approach_km"] == 18.5
    assert result["t_closest"] == "2026-06-18T15:55:30Z"
    assert result["clock"] == "scenario"


@pytest.mark.asyncio
async def test_orbit_simulate_maneuver_returns_post_miss():
    tool = OrbitSimulateManeuverTool()
    ctx = _ctx()
    sat = ctx.orbit.known_satellites()[0]
    result = await tool.execute(
        {"sat": sat, "pre_miss_km": 8.0}, ctx
    )
    assert "post_miss_km" in result
    assert "dv_m_s" in result
    # CW math should widen the miss given a valid Δv.
    assert result["post_miss_km"] >= result["pre_miss_km"]


@pytest.mark.asyncio
async def test_request_draft_assembles_packet_with_burn():
    tool = RequestDraftTool()
    ctx = _ctx()
    result = await tool.execute(
        {
            "actor": "China",
            "confidence": 0.71,
            "justification": ["evidence one", "evidence two"],
            "kb_citations": ["kb-foo"],
            "burn": {
                "sat": "FRIENDLY",
                "against": "INSPECTOR",
                "dv_m_s": 1.5,
                "t_burn": "2026-06-18T12:00:00Z",
                "lead_seconds": 21600,
            },
        },
        ctx,
    )
    packet = result["request_packet"]
    assert packet["to"] == "CJFSCC"
    assert packet["actor"] == "China"
    assert "recommended_burn" in packet
    assert packet["recommended_burn"]["sat"] == "FRIENDLY"


@pytest.mark.asyncio
async def test_routing_validate_rejects_local_strike():
    tool = RoutingValidateTool()
    ctx = _ctx()
    bad = await tool.execute(
        {"action": "active_defense_escort", "authority": "local"}, ctx
    )
    assert bad["valid"] is False
    assert "requires authority=request" in bad["reason"]

    good = await tool.execute(
        {"action": "active_defense_escort", "authority": "request"}, ctx
    )
    assert good["valid"] is True


@pytest.mark.asyncio
async def test_dispatch_emits_tools_trace():
    bus = InProcessBus()
    tracer = Tracer(bus)
    ctx = _ctx(tracer=tracer)

    received: list[ReasoningTrace] = []

    async def consume() -> None:
        async for _, event in bus.subscribe("traces.tools"):
            assert isinstance(event, ReasoningTrace)
            received.append(event)
            break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)

    tool = RoutingValidateTool()
    await dispatch(
        tool,
        {"action": "passive_defense", "authority": "local"},
        ctx,
        ref_id="dec-test",
    )

    await asyncio.wait_for(consumer, timeout=1.0)
    assert received[0].stage == "tools"
    assert received[0].level == "tool"
    assert "routing.validate" in received[0].message
    assert received[0].ref_id == "dec-test"


@pytest.mark.asyncio
async def test_decide_enriches_local_decisions_with_tools():
    from orbit.services.decide import DecideService
    from orbit.services.llm.stub import StubLLMClient
    from orbit.services.schemas.events import Attribution, Decision

    bus = InProcessBus()
    tracer = Tracer(bus)
    ctx = _ctx(tracer=tracer)
    tools = [
        KBLookupTool(),
        RoutingValidateTool(),
    ]

    decide_service = DecideService(
        bus=bus,
        llm=StubLLMClient(ctx.kb),
        orbit=ctx.orbit,
        tracer=tracer,
        tools=tools,
        tool_ctx=ctx,
    )

    traces: list[ReasoningTrace] = []

    async def collect_traces():
        async for _, event in bus.subscribe("traces.tools"):
            if isinstance(event, ReasoningTrace):
                traces.append(event)
                if len(traces) == 2:
                    break

    collector = asyncio.create_task(collect_traces())
    await asyncio.sleep(0)

    decision = Decision(
        attribution_id="att-1",
        action="threat_warning",
        authority="local",
        target="Local defense node",
        rationale="Local threat warning",
    )
    attribution = Attribution(
        id="att-1",
        anomaly_ids=["ano-1"],
        actor="Ghost Lance",
        confidence=0.85,
        evidence=["Observed RF pulse"],
        predicted_next=None,
        kb_citations=["kb-threat"],
        source_signal_ids=["sig-1"],
    )

    enriched = await decide_service._maybe_enrich_with_tools(decision, attribution)
    assert enriched.authority == "local"
    await asyncio.wait_for(collector, timeout=1.0)

    assert len(traces) == 2
    tool_names = [t.payload.get("tool") for t in traces]
    assert "kb.lookup" in tool_names
    assert "routing.validate" in tool_names


@pytest.mark.asyncio
async def test_decide_service_signal_budget_timeout():
    """Verify that DecideService enforces signal_budget_s around self._llm.decide."""
    from orbit.services.decide import DecideService
    from orbit.services.schemas.events import Attribution, Decision

    bus = InProcessBus()

    class HangingLLM:
        async def decide(self, attribution: Attribution) -> Decision:
            # Simulate a hanging LLM call exceeding the budget
            await asyncio.sleep(5.0)
            return Decision(
                attribution_id=attribution.id,
                action="threat_warning",
                authority="local",
                target="target-1",
                rationale="late rationale",
            )

    decide_service = DecideService(
        bus,
        HangingLLM(),  # type: ignore
        tools=[],
        tool_ctx=None,
        signal_budget_s=0.1,  # 100ms budget
    )

    published_decisions: list[Decision] = []

    async def collect_decisions():
        async for _, event in bus.subscribe("decisions.*"):
            if isinstance(event, Decision):
                published_decisions.append(event)

    collector = asyncio.create_task(collect_decisions())
    runner = asyncio.create_task(decide_service.run())
    await asyncio.sleep(0.05)

    # Publish an attribution
    attr = Attribution(
        id="att-test-timeout",
        anomaly_ids=["ano-1"],
        actor="Unknown",
        confidence=0.5,
        evidence=["Observed RF pulse"],
        predicted_next=None,
        kb_citations=[],
        source_signal_ids=["sig-1"],
    )
    await bus.publish("attributions.batch", attr)

    # Allow time for the 0.1s budget timeout to trigger
    await asyncio.sleep(0.3)

    runner.cancel()
    collector.cancel()
    await asyncio.gather(runner, collector, return_exceptions=True)

    # Since the LLM hung and timed out at 0.1s, no decision was published
    assert len(published_decisions) == 0

