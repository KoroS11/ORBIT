# ORBIT Data Provenance & Systems Reconciliation

Verified audit of data pipelines, schemas, math engines, and clock domains across ORBIT.

---

## 1. Verified Pipeline Matrix

### Scenario Signals & Anomaly Generation

| Domain | Raw Signal IDs (`scenarios/`) | Event Type (`payload.event_type`) | Generated Anomaly Kind | Service & Logic |
| :--- | :--- | :--- | :--- | :--- |
| `orbit` | `army-chain-001, x06, 007` | `screening_overlay`, `rpo_close_approach` | `orbital_rpo_risk` | `orbit/services/fusion/__init__.py:466-496` |
| `orbit` | `iran-hormuz-x07, 007` | `overhead_collection_window` | `orbital_collection_risk` | `orbit/services/fusion/__init__.py:421-460` |
| `rf_ew` | `army-chain-002, x01` | `rf_interference` | `rf_anomaly` | `orbit/services/fusion/__init__.py:35` |
| `rf_ew` | `iran-hormuz-x03, 003` | `gnss_jamming_signature` | `rf_gnss_jamming` | `orbit/services/fusion/__init__.py:38` |
| `pnt` | `army-chain-003, x02`<br>`iran-hormuz-x04, 004` | `gps_spoof` | `gnss_spoof` | `orbit/services/fusion/__init__.py:45` |
| `satcom` | `army-chain-009, x04, 006`<br>`iran-hormuz-x06, 006` | `satcom_queue_pressure`, `satcom_degradation` | `satcom_degradation` | `orbit/services/fusion/__init__.py:55, 60` |
| `drone` | `army-chain-004, x05`<br>`iran-hormuz-x05, 005` | `autonomous_relay_handoff` | `drone_relay_handoff` | `orbit/services/fusion/__init__.py:75` |
| `cyber` | `army-chain-x03, 005` | `credential_probe` | `cyber_probe_burst` | `orbit/services/fusion/__init__.py:49` |
| `sda` | `iran-hormuz-x01, 002` | `maritime_space_picture_shift` | `sda_maritime_picture_shift` | `orbit/services/fusion/__init__.py:66` |
| `osint` | `army-chain-008` | `multi_domain_attack_assessment` | `osint_multi_domain_attack` | `orbit/services/fusion/__init__.py:91` |
| `osint` | `iran-hormuz-001` | `blockade_notice` | `osint_blockade_notice` | `orbit/services/fusion/__init__.py:98` |
| `osint` | `iran-hormuz-008` | `space_support_hold_recommendation` | `osint_space_support_hold` | `orbit/services/fusion/__init__.py:93` |
| *Ignored* | `army-chain-010, 011, x07, 012, x08`<br>`iran-hormuz-x02, 009, 010, x08, 011, 012` | `attack_chain_correlation`, `isr_product_quality_gate`, etc. | None (dropped by policy) | `orbit/services/fusion/__init__.py:106-145` (`IGNORED_EVENT_TYPES`) |

### UI Panels & Provenance

| UI Panel | Component | Primary Data Source | Transforming Logic |
| :--- | :--- | :--- | :--- |
| **Mission Alert** | `src/components/MissionAlert.tsx` | Raw Signal + Client Timer | Label from `src/lib/signalEffects.ts:55-74`; text from `src/lib/commanderLanguage.ts:559-623`; elapsed time from client `simElapsedMs`. |
| **AOR Map** | `src/components/AorMap.tsx` | Signal `location` | Coordinates extracted via `src/lib/signalLocation.ts:13-18`; basemap from unauthenticated Esri World Imagery (`AorMap.tsx:880`). |
| **Cesium Globe** | `src/components/CesiumGlobe.tsx` | `data/tle_cache.json` + Maneuver State | SGP4 via `satellite.js`; maneuver delta from `orbit.simulate_maneuver`. |
| **Event Feed** | `src/components/EventFeed.tsx` | Scenario JSONL | Filtered client-side against elapsed playback ticker `simElapsedMs`. |
| **Anomaly Queue** | `src/pages/Operator.tsx` | `FusionService` & `OsintClusterService` | Envelopes received over WS `anomalies.*`; displayed as `{kind} · sev {severity}`. |
| **Decision Rail** | `src/pages/Operator.tsx` | `DecideService` | Emitted over WS `decisions.*`; displays action, authority, and target. |
| **System Actions** | `src/components/ActionLog.tsx` | `Tracer` (`stage === "tools"`) | Emitted during maneuver planning (`orbit/services/decide/__init__.py:182-260`). |
| **OSINT Space** | `src/components/EmbeddingViz.tsx` | `OsintClusterService` | SVD projection of 384D `all-MiniLM-L6-v2` embeddings; threshold is `0.40`. |
| **Reasoning Trace**| `src/components/ReasoningPanel.tsx` | `Tracer` | Real-time terminal log stream with server wall-clock timestamps. |

---

## 2. Verified Simplifications & Discrepancies

1. **Synthetic Satellite Objects vs Catalog**:
   - `UNKNOWN-RSO-441` and `ORBIT-LEO-07` are fictional and absent from `data/tle_cache.json`.
   - `orbit.simulate_maneuver` operates analytically on scenario observables (`pre_miss_km`).
2. **Clock Domains**:
   - **Scenario Clock**: Authored mission timestamps (`2026-06-18` in attack chain, `2026-04-13` in Hormuz) on signals and anomalies.
   - **Server Wall Clock**: Real execution time UTC on reasoning traces and tool outputs.
   - **Simulation Clock (MET)**: Client-side ticker advancing 30s per 3 real seconds in `Brigade.tsx`.
3. **OSINT Cluster Threshold**:
   - Default similarity threshold is `0.40` (`DEFAULT_SIMILARITY_THRESHOLD = 0.40` in `orbit/services/osint_cluster/__init__.py:55`).
4. **Attribution Heuristics (Stub)**:
   - Primary attribution maps dominant anomaly kind using `_KIND_TO_ATTRIBUTION` in `orbit/services/llm/stub.py:36-450`, assigning Russia, China, Unknown, Iran, or Multi-actor.
5. **LLM Provider, Multi-Tier Caching & Quota Reliability**:
   - **Primary Model**: `gemini-3.1-flash-lite` via `GeminiLLMClient`. Configured quotas (as verified in Google AI Studio on 13 October 2026 for this project: 15 RPM, 500 RPD; versus 2.5 Flash at 5 RPM, 20 RPD).
   - **Resolution Order (Strict 5-Tier Lookup)**:
     1. **Local State Cache (`gemini-cache`)**: Read/write disk cache in `ORBIT_STATE_DIR/gemini_cache.json` with atomic temp-file replace, process mutex, and corruption handling (`*.corrupt`).
     2. **Read-Only Seed Cache (`gemini-seed`)**: Committed seed bundle in `data/seed/gemini_cache_seed.json` indexed by model-independent SHA256 of `(system, prompt, schema, PROMPT_VERSION)` ensuring zero live API calls during demonstrations. Offline test calls are simulated/mocked for pipeline verification and safety.
     3. **Daily-Quota Circuit Breaker**: Fast-trips on `RESOURCE_EXHAUSTED` / `GenerateContentRequestsPerDay` errors, avoiding useless retries for 10 minutes.
     4. **Sliding-Window Rate Limiter**: Process-shared 12 RPM limiter with max queue wait of 20 seconds.
     5. **Live Gemini API (`gemini`)**: Live calls with 30s timeout and 0 thinking budget.
     6. **Stub Fallback (`stub`)**: Transparent failover to `StubLLMClient` via `FallbackLLMClient` whenever live API or queue limits fail.
   - **Trace & API Provenance**: Telemetry counters (`cache_hits`, `seed_hits`, `live_calls`, `stub_fallbacks`) exposed in `/health` and displayed live in `DataProvenanceBadge`. Every trace carries provenance (`gemini`, `gemini-cache`, `gemini-seed`, or `stub`).

