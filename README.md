# ORBIT: Cross-domain Attribution and Orbital Protection sYstem (Still in progress)

**Sense · Attribute · Decide.**

> *""Development of missiles does not mean that I am not peaceful. All that we were doing was to create capability since even to promote peace we need strength"*
> — Dr. A.P.J. Abdul Kalam, *Missile Man of India*, March 2025

Space supports **every** fight. ORBIT lets the fight support space.

Virtually every ground operation runs on data piped down from orbit; think ISR, comms, GEOINT, imagery, GPS timing, etc. Adversaries know it: Iran has demonstrated it can sever ~80% of Starlink access to civilians inside its borders, and China fields inspector satellites that shadow allied assets and has physically tugged targets into graveyard orbits. Lose the uplink, lose the fight — and operators today has no single picture of who is doing what to which asset, or what they are authorized to do about it in the next minute.

ORBIT is a tactical multi-domain awareness and decision-support system for the brigade-level analyst, operator, and commander. It fuses orbital, RF/EW, cyber, PNT, SATCOM, drone, HUMINT, and OSINT signals into a single live picture, attributes the threat, recommends an authorized response, and visualizes the outcome — all on hardware that fits in a forward operating base.

- Runs at the **edge** on a single NVIDIA RTX 3090 (or any GPU with ~24 GB VRAM).
- **Sense to decide in under 20 seconds.**
- local-first, with pluggable LLM backends: stub (default), Ollama (local), Anthropic, Gemini.


---

## Architecture

ORBIT is a three-stage agentic pipeline. Each stage publishes typed events to an in-process bus; the FastAPI gateway fans them out to the frontend over a single WebSocket.

| Stage | What it does | Output |
|---|---|---|
| **Sense** | Normalizes heterogeneous feeds (RF, orbital, cyber, PNT, SATCOM, drone, OSINT) into a canonical `Signal`. Fuses signals into `Anomaly` clusters via temporal + spatial + semantic correlation. | `Signal`, `Anomaly` |
| **Attribute** | Multi-agent reasoning: a primary LLM call attributes the anomaly, a red-team call challenges it, and a reconciler produces a calibrated final `Attribution` with confidence. | `Attribution`, `AttributionChallenge` |
| **Decide** | Tool-using LLM agent calls real tools (`kb.lookup`, `orbit.compute_close_approach` (Skyfield SGP4 solver), `orbit.simulate_maneuver` (Clohessy-Wiltshire equations), `request.draft`, `routing.validate`) to produce an actionable `Decision` with authority routing. | `Decision`, `RequestPacket` |

Interpretability: Every reasoning step emits a `ReasoningTrace` event so the operator sees *why* — not just *what* — on a live terminal-style panel.

The frontend is a React + Cesium + MapLibre console with two views:
- **Brigade COP** — multi-domain fusion picture, OSINT clustering, scenario timeline, reasoning trace, and approval banner.
- **Operator Console** — review surface where the operator accepts or denies the decide-stage recommendation. Accept triggers an interactive Cesium animation showing the maneuver against the live N2YO satellite catalog (plane-change burn, kinetic-strike Bezier, or link-interdiction beam, depending on the action).

---

## Where the data comes from

| Source | Domain | Notes |
|---|---|---|
| **CelesTrak GP catalog** ([`data/source_registry.json`](data/source_registry.json), [`scripts/fetch_orbital_cache.py`](scripts/fetch_orbital_cache.py)) | orbit / SDA | Authoritative TLE/OMM feeds for GEO, GPS-OPS, Starlink, Planet, and the GPZ+ "objects of interest" group, downloaded offline from `celestrak.org`. Skyfield-backed SGP4 propagation evaluates close-approach geometry from the local cache. |
| **N2YO position feed** ([`public/orbital/`](public/orbital/), [`data/tle_cache.json`](data/tle_cache.json)) | orbit / SDA | Per-satellite position snapshots for AEHF, MUOS, WGS, SBIRS, GSSAP, GPS-3 (US) and Yaogan / Cosmos (CHN/RUS), populated offline by `scripts/fetch_n2yo_satellite.py`. Drives the Cesium globe's animated orbital tracks. |
| **OSINT corpus** | open-source | Sentence-transformer (`all-MiniLM-L6-v2`, 384-dim) embeddings, cosine-clustered at 0.40, PCA-projected to 2D for the embedding panel. |
| **Hand-authored scenarios** ([`scenarios/*.jsonl`](scenarios/)) | RF/EW, cyber, PNT, SATCOM, drone, HUMINT | 11 deterministic JSONL beats covering CENTCOM, Army, and regional vignettes replayed by the engine. |
| **Procedural variants** ([`bench/scenarios/`](bench/)) | synthetic | ~40 perturbations of the 11 seeds (miss distance, lead time, signal mix) for the benchmark harness. |
| **KB** ([`kb/`](kb/), [`data/kb_seed_entries.json`](data/kb_seed_entries.json)) | tradecraft | Actor capabilities, doctrine references, and routing rules used by `kb.lookup`. |

In runtime operation:
- **Signals**: Domain signals come from hand-authored and procedurally generated scenario files replayed through the engine's event bus.
- **Orbital data**: CelesTrak TLEs and N2YO ephemeris feeds are refreshed by offline scripts into cached files (`data/`, `public/orbital/`) and are not queried at runtime.
- **Pipeline & models**: The `all-MiniLM-L6-v2` embedding model and the sense/attribute/decide pipeline run for real on that input. The LLM reasoning stage runs the deterministic stub by default, or pluggable Gemini, Ollama, or Anthropic backends when configured.

Every record flows through the same canonical `Signal` schema.

---

## Running locally

### Prerequisites

- Python ≥ 3.12 with [`uv`](https://github.com/astral-sh/uv). 
- Node ≥ 20.
- [Ollama](https://ollama.com) running locally with **Gemma 4 E2B** pulled (if using the local Ollama backend).
- One NVIDIA RTX 3050 (or any GPU with ~12 GB VRAM) for inference.

### Environment setup
```
uv venv
source .venv/bin/activate
uv sync
```

### LLM Backends

ORBIT supports pluggable LLM backends configured via `ORBIT_LLM`:

- **Stub (default)**: `ORBIT_LLM=stub` — offline deterministic mocks for testing and development.
- **Ollama (local)**: `ORBIT_LLM=ollama` — local inference (`ORBIT_OLLAMA_URL=http://localhost:11434`, `ORBIT_OLLAMA_MODEL=gemma4:26b`, `ORBIT_OLLAMA_TIMEOUT_S=180`).
- **Anthropic**: `ORBIT_LLM=anthropic` (`ANTHROPIC_API_KEY`, `ORBIT_ANTHROPIC_MODEL=claude-sonnet-4-6`).
- **Gemini**: `ORBIT_LLM=gemini` (`GEMINI_API_KEY`, `ORBIT_GEMINI_MODEL=gemini-2.5-flash`, `ORBIT_GEMINI_TIMEOUT_S=60`, `ORBIT_LLM_FALLBACK=1`).

### Backend gateway
In one terminal, run the following to start the backend:
```bash
uv run uvicorn orbit.api:app --host 0.0.0.0 --port 8000
```

The gateway boots the in-process bus, the three services, and exposes:
- `GET /health` — liveness check
- `GET /scenarios` — list available scenarios
- `POST /scenarios/{name}/replay` — replay a scenario beat
- `POST /signals` — ingest a domain signal
- `GET /kb` — list loaded knowledge base entries
- `POST /stress` — simulate domain degradation
- `WS /ws` — Signal / Anomaly / Attribution / Decision / UIEvent / ReasoningTrace fanout

### Frontend
In a second terminal, run the following to start the frontend:
```bash
npm install
npm run dev   # http://localhost:5173
```

Set `VITE_ORBIT_API_URL=http://localhost:8000` in `.env.local` if the gateway runs on a different host.
You can view and interact with the frontend by going to `http://localhost:5173` in a browser.

### One-shot verification

```bash
uv run python scripts/verify.py scenarios/army_multidomain_attack_chain.jsonl
```

Replays a scenario through the engine in-process, asserts the expected sense → attribute → decide chain, and prints the reasoning trace.

### Benchmark

```bash
uv run python -m bench.run
```

Runs the 51-scenario harness (11 seeds + ~40 procedural variants) and writes a scorecard with attribution accuracy, calibration, and p50/p95 latency.

### Stress mode

The operator panel can simulate domain loss:

```bash
curl -X POST http://localhost:8000/stress \
  -H "Content-Type: application/json" \
  -d '{"blocked_domains": ["pnt", "satcom"]}'
```

The reasoning panel will surface `[stress] PNT data unavailable, lowering confidence` in real time and the engine will route to `threat_warning` when correlations are starved.

---

## Why local-first

| Constraint | ORBIT's answer |
|---|---|
| No reliable backhaul at the edge | Full pipeline runs on a single GPU; no API calls leave the box. |
| Adversarial denial of comms | Stress-mode degrades gracefully; reasoning panel narrates the loss. |
| Sensitive sensor data | Nothing is sent to a hyperscaler because Ollama hosts the model in-process. |
| Time-critical decisions | <20 s sense-to-decide on Gemma 4 E2B. |

---

## Repo layout

```
orbit/                  # Python engine (services, schemas, API gateway)
  api/                 # FastAPI gateway + WebSocket fanout
  services/            # fusion, attrib (multi-agent), decide (tool-using), llm, orbit, kb, traces
src/                   # React + Cesium + MapLibre console
  components/          # Brigade COP + Operator Console + reasoning panel + maneuver demo
  lib/n2yoSatelliteLayer.ts  # SGP4-driven orbital animation
scenarios/             # 11 hand-authored JSONL beats
bench/                 # benchmark harness + scorecard
data/                  # KB seeds, source registry, orbital cache metadata
public/orbital/        # N2YO position caches per NORAD ID
kb/                    # capability + routing knowledge base
```

---

## Benchmark

ORBIT ships with its own evaluation harness ([`bench/`](bench/)), which contains 11 hand-labelled seed scenarios plus ~40 procedural variants, each with ground-truth `expected_actor` / `expected_action` / `expected_authority` / `expected_band` labels. `bench/run.py` boots the engine in-process, replays every scenario, and produces a scorecard covering attribution accuracy, action match, authority routing, calibration (correct ↔ confidence band), and p50/p95 latency. 

We're aware that small open-weight models leave headroom on multi-domain attribution compared to large cloud-hosted LLMs. We're interested in pursuing further research in this direction and are happy to move forward with the right collaborators on this.

