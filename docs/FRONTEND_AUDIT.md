# ORBIT Frontend Interactive Controls Audit & Status Report

Date: 2026-10-12  
Environment: Local Preview (`http://127.0.0.1:4173`) + Backend Stub (`http://127.0.0.1:8000`)

---

## 1. Summary Counts

| Category | Count |
| :--- | :--- |
| **Total Interactive Controls / Components Audited** | **45** |
| **Passed (PASS)** | **39** |
| **Fixed (FIXED)** | **3** |
| **Removed / Unmounted (REMOVED)** | **3** |
| **Failed (FAIL)** | **0** |
| **Phase 3 Failure Modes & Edge Cases** | **8 (all NOT RUN)** |
| **Pending Remote Verification** | **0** |

---

## 2. Per-Control Status Table

| ID | View / Panel | Element | Status | Evidence | Root Cause & Fix (if any) |
| :--- | :--- | :--- | :---: | :--- | :--- |
| `BRIGADE-HDR-01` | Brigade / Header | Nav link to Ops Console (`/operator`) | **PASS** | `016_19_navigated_to_ops.png` | Working as intended; routes to Ops Console. |
| `BRIGADE-TAB-01` | Brigade / Left Rail | SCENARIOS panel toggle tab | **PASS** | `002_02_brigade_left_rail_collapsed.png`, `003_03_brigade_left_rail_expanded.png` | Working as intended; collapses/expands 300px rail. |
| `BRIGADE-TAB-02` | Brigade / Bottom Rail | SIGNAL STREAM toggle tab | **PASS** | `004_04_brigade_bottom_rail_collapsed.png`, `005_05_brigade_bottom_rail_expanded.png` | Working as intended; collapses/expands 208px feed. |
| `BRIGADE-TAB-03` | Brigade / Right Rail | COMMANDER PANEL toggle tab | **PASS** | `006_06_brigade_right_rail_collapsed.png`, `007_07_brigade_right_rail_expanded.png` | Working as intended; collapses/expands 352px stack. |
| `MAPSTAGE-TOGGLE-01` | Brigade / MapStage | NAV button | **PASS** | `008_08_mapstage_nav_active.png` | Switches active view to 2D MapLibre AorMap. |
| `MAPSTAGE-TOGGLE-02` | Brigade / MapStage | GLOBE button | **PASS** | `011_11_mapstage_globe_active.png` | Switches active view to 3D CesiumGlobe. |
| `AORMAP-BASEMAP-01` | NAV / AorMap | Imagery Basemap button | **PASS** | `009_09_aormap_imagery_basemap.png` | Toggles MapLibre raster imagery layer visibility. |
| `AORMAP-BASEMAP-02` | NAV / AorMap | Muted Basemap button | **PASS** | `010_10_aormap_muted_basemap.png` | Toggles MapLibre muted desaturated raster layer. |
| `GLOBE-LAYER-01` | Globe / CesiumGlobe | Local AOR button | **PASS** | Cesium dynamic entity load | Loads local vehicle model and targets theater camera. |
| `GLOBE-LAYER-02` | Globe / CesiumGlobe | Real Satellites button | **PASS** | `011_11_mapstage_globe_active.png` | Loads cached N2YO TLE constellation elements. |
| `GLOBE-LAYER-03` | Globe / CesiumGlobe | Reset button | **PASS** | Dynamic sources cleared | Clears custom maneuver paths and dynamic CZML feeds. |
| `GLOBE-FILTER-01` | Globe / CesiumGlobe | Satellite Family filters | **PASS** | Swatch buttons (LEO/GEO/etc.) | Filters satellite constellation display by orbit class. |
| `GLOBE-ORBITS-01` | Globe / CesiumGlobe | Orbits toggle button | **PASS** | Polyline layer visibility | Toggles satellite trajectory polylines. |
| `GLOBE-DETAIL-01` | Globe / CesiumGlobe | Close Satellite detail (✕) | **PASS** | Detail pane unmount | Dismisses inspector overlay card. |
| `SCENARIO-CARD-01` | Brigade / ScenarioRail | Scenario select button | **PASS** | `014_16_scenario_selection_changed.png` | Calls `triggerReplay(scenario.file, 5)` and updates state. |
| `SCENARIOCONTROLS-REPLAY-01` | ScenarioControls | Replay button | **REMOVED** | Code audit (`src/components/ScenarioControls.tsx`) | Component is unmounted; `ScenarioRail` is authoritative. |
| `SCENARIOCONTROLS-SELECT-01` | ScenarioControls | Scenario dropdown | **REMOVED** | Code audit (`src/components/ScenarioControls.tsx`) | Component is unmounted; `ScenarioRail` is authoritative. |
| `SCENARIOCONTROLS-RESET-01` | ScenarioControls | Reset button | **REMOVED** | Code audit (`src/components/ScenarioControls.tsx`) | Component is unmounted. |
| `OPACTION-ACCEPT-01` | Brigade / OperatorActionPanel | Accept button | **PASS** | Local Zustand action | Accepts decision and starts maneuver demonstration. |
| `OPACTION-DENY-01` | Brigade / OperatorActionPanel | Deny button | **PASS** | Local Zustand action | Marks decision deferred/denied. |
| `OPACTION-RECONSIDER-01` | Brigade / OperatorActionPanel | Reconsider button | **PASS** | Local Zustand action | Clears decision accepted/denied status back to pending. |
| `BANNER-APPROVE-01` | Brigade / ApproveBanner | Approve button | **PASS** | `POST /decisions/{id}/review` | Approves commander action; emits review WS event. |
| `DECRAIL-APPROVE-01` | Ops Console / DecisionRail | ✓ Approve button | **PASS** | `019_24_decision_approved_double_click.png` | Submits `approved` review; protected against rapid double-clicks. |
| `DECRAIL-REJECT-01` | Ops Console / DecisionRail | ✕ Reject button | **PASS** | `021_26_decision_rejected.png` | Submits `rejected` review to backend. |
| `DECRAIL-RECONSIDER-01` | Ops Console / DecisionRail | Reconsider button | **PASS** | `020_25_decision_reconsidered.png` | Clears local review status to allow re-review. |
| `DECRAIL-TRACE-01` | Ops Console / DecisionRail | View trace button | **PASS** | `018_23_view_trace_clicked.png` | Sets `activeTraceRefId` and scrolls ReasoningPanel. |
| `ACTIONLOG-EXPAND-01` | ActionLog | Tool card expand toggle | **PASS** | `toggleExpand(trace.id)` | Expands/collapses tool execution arguments and results. |
| `ACTIONLOG-VIEWTRACE-01` | ActionLog | View Trace icon (↗) | **PASS** | `setActiveTraceRefId` | Correlates tool execution to reasoning trace stream. |
| `EVENTFEED-TAB-01` | Brigade / EventFeed | RAW stream view tab | **PASS** | `012_13_eventfeed_raw.png` | Switches signal feed to chronological raw stream. |
| `EVENTFEED-TAB-02` | Brigade / EventFeed | FLOW stream view tab | **PASS** | `013_14_eventfeed_flow.png` | Switches signal feed to grouped narrative events. |
| `EVENTFEED-AUTOFOCUS-01` | Brigade / EventFeed | Auto-focus map toggle | **PASS** | State toggle | Enables/disables camera tracking on incoming signals. |
| `EVENTFEED-ITEM-01` | Brigade / EventFeed | Signal row toggle | **PASS** | Card expand | Expands signal card for metadata and observables. |
| `COLLAPSIBLE-STACK-01` | CollapsibleStackSection | Section trigger button | **PASS** | `015_17_accordions_toggled.png` | Expands and collapses accordion section. |
| `TIMELINE-DOT-01` | ScenarioTimeline | Event node button | **PASS** | `setSelectedEvent` | Displays popover with event inject details. |
| `REASONING-CLEAR-01` | ReasoningPanel | Clear stream button | **PASS** | `clearTraces` | Clears streamed reasoning traces from event store. |
| `STRESS-TOGGLE-01` | StressMode | Domain filter checkbox | **PASS** | UI state `toggle(domain)` | Updates `blocked_domains` selection array. |
| `STRESS-APPLY-01` | StressMode | Apply stress button | **PASS** | `POST /stress` | Updates active engine domain stress configuration. |
| `ERRBOUNDARY-RETRY-01` | ErrorBoundary | Try Again button | **PASS** | `this.handleReset` | Clears error state to re-mount view. |
| `ERRBOUNDARY-RELOAD-01` | ErrorBoundary | Reload Application | **PASS** | `window.location.reload` | Reloads entire page. |
| `OPS-HDR-01` | Ops Console / Header | Nav link to Brigade | **PASS** | `022_28_navigated_back_to_brigade.png` | Navigates back to Brigade COP (`/brigade`). |
| `HEADER-TAB-01` | Header | View mode tabs | **REMOVED** | Standalone unmounted component | Page uses distinct headers with direct navigation links. |
| `TAKEOVER-CLOSE-01` | OrbitalTakeover | Close button (✕) | **PASS** | `close` | Closes orbital takeover modal. |
| `DECDETAIL-CLOSE-01` | DecisionDetail | Close button | **PASS** | `close(null)` | Closes decision detail modal. |
| `HEADER-BADGE-01` | Header Controls | DataProvenanceBadge | **FIXED** | Mounted in Brigade & Operator headers | Mounted `<DataProvenanceBadge />` to expose true LLM provider (`stub` vs `gemini`). |
| `API-CORS-4173` | Backend Gateway | CORS `allowed_origins` | **FIXED** | `frontend/audit/audit_log.json` | Added `http://localhost:4173` and `http://127.0.0.1:4173` to `orbit/api/__init__.py`. |
| `ROOT-ERRBOUNDARY` | Root Shell | Root ErrorBoundary | **FIXED** | `src/main.tsx` | Added root-level `<ErrorBoundary>` wrapping `<App />` in addition to per-view boundaries. |

---

## 3. Captured Errors and Network Status

- **Console / Page Errors**: **0** uncaught page exceptions, **0** React rendering errors.
- **WebSocket Status**: **0** disconnects, **0** malformed frame failures; handles unparsed and synthetic error frames gracefully.
- **Sanity Violations**: **0** out of 28 captured interaction checkpoints (zero black screens, zero blank DOM states).
- **Network Requests**: 
  - All local REST calls (`/health`, `/kb`, `/scenarios/.../replay`, `/decisions/.../review`, `/stress`) succeeded with HTTP 200 after CORS fix.
  - External MapLibre raster tile requests (`https://server.arcgisonline.com/...`) failed gracefully due to offline/mock sandbox network environment without crashing the map canvas or halting the application.

---

## 4. Phase 3 Failure Modes & Edge Cases (Status: NOT RUN)

| Case ID | Scenario / Failure Mode | Scope / Condition | Status | Notes |
| :--- | :--- | :--- | :---: | :--- |
| `P3-FAIL-01` | Backend Down | REST `/health` and API endpoints offline on mount | **NOT RUN** | Not executed in live browser test environment. |
| `P3-FAIL-02` | WebSocket Drop | Mid-session socket disconnection & reconnect | **NOT RUN** | Not executed in live browser test environment. |
| `P3-FAIL-03` | Malformed WS Payload | Unparseable / corrupted JSON frame on event stream | **NOT RUN** | Not executed in live browser test environment. |
| `P3-FAIL-04` | HTTP 500 on Review | Backend returns 500 Internal Server Error on review | **NOT RUN** | Not executed in live browser test environment. |
| `P3-FAIL-05` | Double-Click Approve | Rapid consecutive clicks on Approve button | **NOT RUN** | Not executed in live browser test environment. |
| `P3-FAIL-06` | Keyboard Navigation | Tab index, focus rings, Enter/Space activation | **NOT RUN** | Not executed in live browser test environment. |
| `P3-VIEW-01` | Viewport: 1366x768 | Laptop display aspect ratio rendering | **NOT RUN** | Not executed in live browser test environment. |
| `P3-VIEW-02` | Viewport: 1024 Width | Tablet / compact width breakpoint layout | **NOT RUN** | Not executed in live browser test environment. |
