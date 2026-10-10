"""FastAPI gateway exposing the in-process ORBIT engine to browser clients.

Endpoints:

* ``GET  /health``                     — liveness
* ``GET  /scenarios``                  — list checked-in scenario JSONL filenames
* ``POST /scenarios/{name}/replay``    — start a ScenarioReplayService for that beat
* ``POST /signals``                    — accept a Signal and publish to the bus
* ``WS   /ws``                         — fan out every bus event as a JSON envelope:
                                         ``{topic, kind, data}``

The app boots an in-process engine in its lifespan; every connected WebSocket
gets the same firehose. Brigade vs Operator filtering is the client's job.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from orbit._engine import build_engine, resolve_provider, start_engine_tasks
from orbit.services.scenario_replay import ScenarioReplayService
from orbit.services.schemas.events import Signal

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = ROOT / "scenarios"

# Topic patterns we forward to clients, paired with the kind tag they get
# tagged with in the WebSocket envelope.
_FANOUT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("signals.*", "signal"),
    ("anomalies.*", "anomaly"),
    ("attributions.*", "attribution"),
    ("decisions.*", "decision"),
    ("ui_events.*", "ui_event"),
    ("traces.*", "trace"),
    ("embeddings.*", "embedding"),
    ("system.*", "reset"),
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    load_dotenv()
    provider = resolve_provider(llm_flag=None)
    log.info("ORBIT API starting (llm=%s)", provider)

    app.state.blocked_domains: set[str] = set()
    app.state.provider = provider
    engine = build_engine(
        provider=provider,
        blocked_domains_provider=lambda: app.state.blocked_domains,
    )
    app.state.engine = engine
    app.state.clients = set()
    app.state.replay_task = None
    app.state.engine_tasks = start_engine_tasks(engine)
    app.state.fanout_tasks = [
        asyncio.create_task(
            _fanout(engine.bus, pattern, kind, app.state.clients),
            name=f"fanout-{kind}",
        )
        for pattern, kind in _FANOUT_PATTERNS
    ]

    try:
        yield
    finally:
        log.info("ORBIT API shutting down")
        replay = app.state.replay_task
        if replay is not None and not replay.done():
            replay.cancel()
        for task in (*app.state.fanout_tasks, *app.state.engine_tasks):
            task.cancel()
        await asyncio.gather(
            *(t for t in app.state.fanout_tasks),
            *(t for t in app.state.engine_tasks),
            return_exceptions=True,
        )
        engine.bus.close()


async def _fanout(bus, pattern: str, kind: str, clients: set[WebSocket]) -> None:
    """Forward every bus event matching *pattern* to every connected client."""
    async for topic, event in bus.subscribe(pattern):
        if hasattr(event, "model_dump"):
            data: Any = event.model_dump(mode="json")
        else:
            data = event
        envelope = {"topic": topic, "kind": kind, "data": data}
        # Iterate over a snapshot — clients can disconnect mid-fanout.
        for ws in list(clients):
            try:
                await ws.send_json(envelope)
            except Exception:
                clients.discard(ws)


def create_app() -> FastAPI:
    app = FastAPI(title="ORBIT Engine Gateway", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        engine = getattr(app.state, "engine", None)
        provider = getattr(app.state, "provider", "stub")
        fallback_active = False
        if engine and hasattr(engine, "llm"):
            from orbit.services.llm.gemini_client import FallbackLLMClient

            if isinstance(engine.llm, FallbackLLMClient):
                fallback_active = True
        provider_str = provider

        osint = {
            "service_attached": False,
            "model_loaded": False,
            "model_name": None,
            "embedding_dim": 0,
            "window_size": 0,
            "clusters_seen": 0,
            "similarity_threshold": None,
        }
        if engine is not None:
            cluster = engine.osint_cluster
            osint["service_attached"] = True
            osint["model_loaded"] = cluster._encoder is not None
            osint["model_name"] = cluster._model_name
            osint["embedding_dim"] = cluster._encoder_dim
            osint["window_size"] = len(cluster._window)
            osint["clusters_seen"] = cluster._next_cluster_id
            osint["similarity_threshold"] = cluster._similarity_threshold
        return {
            "status": "ok",
            "provider": provider_str,
            "stub_fallback_active": fallback_active,
            "llm": engine.llm.__class__.__name__ if engine else None,
            "kb_entries": len(engine.kb) if engine else 0,
            "clients": len(getattr(app.state, "clients", ())),
            "osint_cluster": osint,
        }

    @app.get("/scenarios")
    async def list_scenarios() -> list[str]:
        if not SCENARIOS_DIR.exists():
            return []
        return sorted(p.name for p in SCENARIOS_DIR.glob("*.jsonl"))

    @app.get("/kb")
    async def get_kb() -> dict[str, Any]:
        """Return the loaded knowledge base entries.

        The Operator view uses this to resolve kb-* citation ids to full
        title/summary/decision-implications cards client-side without baking
        the JSON into the frontend bundle.
        """
        engine = app.state.engine
        return {
            "entries": [e.model_dump(mode="json") for e in engine.kb.all_entries()],
        }

    @app.get("/fixture/ui_events")
    async def get_fixture_ui_events() -> dict[str, Any]:
        """Serve the canned UIEvent fixture for the frontend's offline mode."""
        import json

        path = ROOT / "data" / "expected_ui_events.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="fixture missing")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/reset")
    async def reset_engine() -> dict[str, Any]:
        prev = app.state.replay_task
        if prev is not None and not prev.done():
            prev.cancel()
            app.state.replay_task = None
        if hasattr(app.state, "engine"):
            app.state.engine.reset()
            await app.state.engine.bus.publish("system.reset", {"type": "reset"})
        app.state.decision_reviews = {}
        return {"status": "reset"}

    @app.get("/decisions/reviews")
    async def get_decision_reviews() -> dict[str, Any]:
        reviews = getattr(app.state, "decision_reviews", {})
        return {"reviews": reviews}

    @app.post("/decisions/{decision_id}/review")
    async def review_decision(
        decision_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        status = payload.get("status", "approved")
        actor = payload.get("actor", "OPERATOR-01")
        timestamp = payload.get("timestamp") or datetime.now(UTC).isoformat()
        review = {
            "decision_id": decision_id,
            "status": status,
            "actor": actor,
            "timestamp": timestamp,
        }
        if not hasattr(app.state, "decision_reviews"):
            app.state.decision_reviews = {}
        app.state.decision_reviews[decision_id] = review

        if hasattr(app.state, "engine"):
            engine = app.state.engine
            await engine.bus.publish(
                f"decisions.{status}",
                {
                    "type": "decision_review",
                    "decision_id": decision_id,
                    "status": status,
                    "actor": actor,
                    "timestamp": timestamp,
                },
            )
            tracer = getattr(engine, "tracer", None)
            if tracer is not None:
                await tracer.emit(
                    "decide",
                    "decision",
                    f"Decision {decision_id} marked as {status.upper()} by {actor}",
                    ref_id=decision_id,
                    status=status,
                    actor=actor,
                )
        return review

    @app.post("/scenarios/{name}/replay")
    async def replay_scenario(name: str, speed: float = 5.0) -> dict[str, Any]:
        path = SCENARIOS_DIR / name
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"scenario not found: {name}")

        prev = app.state.replay_task
        if prev is not None and not prev.done():
            prev.cancel()
            app.state.replay_task = None

        if hasattr(app.state, "engine"):
            app.state.engine.reset()
            await app.state.engine.bus.publish("system.reset", {"type": "reset"})

        replay = ScenarioReplayService(
            app.state.engine.bus,
            path,
            speed=speed,
            max_delay_s=0.5,
        )
        app.state.replay_task = asyncio.create_task(
            replay.run(), name=f"replay-{name}"
        )
        return {"status": "replaying", "scenario": name, "speed": speed}

    @app.post("/signals")
    async def post_signal(signal: Signal) -> dict[str, Any]:
        await app.state.engine.bus.publish(f"signals.{signal.domain}", signal)
        return {"status": "queued", "id": signal.id}

    _ALLOWED_DOMAINS = {
        "sda", "orbit", "osint", "humint", "rf_ew", "cyber",
        "pnt", "satcom", "drone", "terrain",
    }

    @app.get("/stress")
    async def get_stress() -> dict[str, Any]:
        return {"blocked_domains": sorted(app.state.blocked_domains)}

    @app.post("/stress")
    async def post_stress(payload: dict[str, Any]) -> dict[str, Any]:
        raw = payload.get("blocked_domains", [])
        if not isinstance(raw, list):
            raise HTTPException(
                status_code=400, detail="blocked_domains must be a list"
            )
        invalid = [d for d in raw if d not in _ALLOWED_DOMAINS]
        if invalid:
            raise HTTPException(
                status_code=400, detail=f"unknown domains: {invalid}"
            )
        app.state.blocked_domains = set(raw)
        return {"blocked_domains": sorted(app.state.blocked_domains)}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        app.state.clients.add(websocket)
        try:
            while True:
                # Block on receive_text so the connection stays open; the
                # client doesn't actually need to send anything.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("websocket error")
        finally:
            app.state.clients.discard(websocket)

    return app


app = create_app()
