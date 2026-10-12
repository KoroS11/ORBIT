import re
from pathlib import Path

# Static Inventory definitions for all interactive elements in ORBIT
INVENTORY = [
    # --- BRIGADE COP / HEADER & GLOBAL CONTROLS ---
    {
        "id": "BRIGADE-HDR-01",
        "view": "Brigade / Header",
        "element": "Nav link to Ops Console",
        "handler": "src/pages/Brigade.tsx:222",
        "expected": "Navigates browser to /operator (Ops Console)",
        "backend": "None (Client-side / Browser navigation)",
        "flag": "PASS"
    },
    {
        "id": "BRIGADE-TAB-01",
        "view": "Brigade / Left Rail",
        "element": "SCENARIOS panel toggle tab",
        "handler": "src/pages/Brigade.tsx:260",
        "expected": "Toggles scenario rail collapsed/expanded state",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "BRIGADE-TAB-02",
        "view": "Brigade / Bottom Rail",
        "element": "SIGNAL STREAM panel toggle tab",
        "handler": "src/pages/Brigade.tsx:298",
        "expected": "Toggles event feed stream collapsed/expanded state",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "BRIGADE-TAB-03",
        "view": "Brigade / Right Rail",
        "element": "COMMANDER PANEL toggle tab",
        "handler": "src/pages/Brigade.tsx:358",
        "expected": "Toggles commander decision stack collapsed/expanded state",
        "backend": "None (UI state)",
        "flag": "PASS"
    },

    # --- MAP STAGE (NAV / GLOBE TOGGLES) ---
    {
        "id": "MAPSTAGE-TOGGLE-01",
        "view": "Brigade / MapStage",
        "element": "NAV button",
        "handler": "src/components/MapStage.tsx:94",
        "expected": "Switches workspace view to 2D MapLibre AorMap ('nav')",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "MAPSTAGE-TOGGLE-02",
        "view": "Brigade / MapStage",
        "element": "GLOBE button",
        "handler": "src/components/MapStage.tsx:102",
        "expected": "Switches workspace view to 3D CesiumGlobe ('globe')",
        "backend": "None (UI state)",
        "flag": "PASS"
    },

    # --- AOR MAP (2D TACTICAL MAP) ---
    {
        "id": "AORMAP-BASEMAP-01",
        "view": "NAV / AorMap",
        "element": "Imagery Basemap button",
        "handler": "src/components/AorMap.tsx:1345",
        "expected": "Switches MapLibre tile style to satellite imagery",
        "backend": "None (MapLibre style update)",
        "flag": "PASS"
    },
    {
        "id": "AORMAP-BASEMAP-02",
        "view": "NAV / AorMap",
        "element": "Muted Basemap button",
        "handler": "src/components/AorMap.tsx:1352",
        "expected": "Switches MapLibre tile style to muted tactical style",
        "backend": "None (MapLibre style update)",
        "flag": "PASS"
    },

    # --- CESIUM GLOBE (3D SPACE GLOBE) ---
    {
        "id": "GLOBE-LAYER-01",
        "view": "Globe / CesiumGlobe",
        "element": "Local AOR button",
        "handler": "src/components/CesiumGlobe.tsx:1750",
        "expected": "Loads high-resolution vehicle model in local theater",
        "backend": "None (Cesium dynamic entity loading)",
        "flag": "PASS"
    },
    {
        "id": "GLOBE-LAYER-02",
        "view": "Globe / CesiumGlobe",
        "element": "Real Satellites button",
        "handler": "src/components/CesiumGlobe.tsx:1757",
        "expected": "Fetches active TLE constellations via N2YO layer / cache",
        "backend": "None / Cached TLEs (lib/n2yoSatelliteLayer)",
        "flag": "PASS"
    },
    {
        "id": "GLOBE-LAYER-03",
        "view": "Globe / CesiumGlobe",
        "element": "Reset Dynamic Sources button",
        "handler": "src/components/CesiumGlobe.tsx:1762",
        "expected": "Clears simulated satellite maneuver paths and custom entities",
        "backend": "None (UI / Cesium state)",
        "flag": "PASS"
    },
    {
        "id": "GLOBE-FILTER-01",
        "view": "Globe / CesiumGlobe",
        "element": "Satellite Family filter buttons (all, LEO, GEO, etc.)",
        "handler": "src/components/CesiumGlobe.tsx:1778",
        "expected": "Filters rendered satellites by orbit/constellation family",
        "backend": "None (Cesium entity visibility)",
        "flag": "PASS"
    },
    {
        "id": "GLOBE-ORBITS-01",
        "view": "Globe / CesiumGlobe",
        "element": "Orbits toggle button",
        "handler": "src/components/CesiumGlobe.tsx:1810",
        "expected": "Toggles orbital polyline path rendering on/off",
        "backend": "None (Cesium polyline visibility)",
        "flag": "PASS"
    },
    {
        "id": "GLOBE-DETAIL-01",
        "view": "Globe / CesiumGlobe",
        "element": "Selected Satellite Close button (✕)",
        "handler": "src/components/CesiumGlobe.tsx:1826",
        "expected": "Closes satellite detail inspection overlay",
        "backend": "None (UI state)",
        "flag": "PASS"
    },

    # --- SCENARIO RAIL & CONTROLS ---
    {
        "id": "SCENARIO-CARD-01",
        "view": "Brigade / ScenarioRail",
        "element": "Scenario item selection button",
        "handler": "src/components/ScenarioRail.tsx:98",
        "expected": "Selects active scenario, resets store, triggers backend replay",
        "backend": "POST /scenarios/{name}/replay?speed=5, WS system.reset",
        "flag": "PASS"
    },
    {
        "id": "SCENARIOCONTROLS-REPLAY-01",
        "view": "Unmounted Component / ScenarioControls",
        "element": "Replay button",
        "handler": "src/components/ScenarioControls.tsx:54",
        "expected": "Replays selected scenario",
        "backend": "POST /scenarios/{name}/replay",
        "flag": "UNMOUNTED (Utility component not in view hierarchy)"
    },
    {
        "id": "SCENARIOCONTROLS-SELECT-01",
        "view": "Unmounted Component / ScenarioControls",
        "element": "Scenario dropdown select",
        "handler": "src/components/ScenarioControls.tsx:64",
        "expected": "Selects scenario name for replay",
        "backend": "GET /scenarios",
        "flag": "UNMOUNTED (Utility component not in view hierarchy)"
    },
    {
        "id": "SCENARIOCONTROLS-RESET-01",
        "view": "Unmounted Component / ScenarioControls",
        "element": "Reset button",
        "handler": "src/components/ScenarioControls.tsx:82",
        "expected": "Resets engine and local state",
        "backend": "POST /reset, WS system.reset",
        "flag": "UNMOUNTED (Utility component not in view hierarchy)"
    },

    # --- OPERATOR ACTION PANEL (LEFT RAIL IN BRIGADE) ---
    {
        "id": "OPACTION-ACCEPT-01",
        "view": "Brigade / OperatorActionPanel",
        "element": "Accept button",
        "handler": "src/components/OperatorActionPanel.tsx:103",
        "expected": "Accepts decision, starts Cesium maneuver animation",
        "backend": "Local Zustand state + startManeuverDemo",
        "flag": "PASS"
    },
    {
        "id": "OPACTION-DENY-01",
        "view": "Brigade / OperatorActionPanel",
        "element": "Deny button",
        "handler": "src/components/OperatorActionPanel.tsx:136",
        "expected": "Denies (defers) decision",
        "backend": "Local Zustand state deferDecision",
        "flag": "PASS"
    },
    {
        "id": "OPACTION-RECONSIDER-01",
        "view": "Brigade / OperatorActionPanel",
        "element": "Reconsider button",
        "handler": "src/components/OperatorActionPanel.tsx:149",
        "expected": "Clears accepted/denied status back to pending",
        "backend": "Local Zustand state clearDecisionStatus",
        "flag": "PASS"
    },

    # --- APPROVE BANNER (RIGHT STACK IN BRIGADE) ---
    {
        "id": "BANNER-APPROVE-01",
        "view": "Brigade / ApproveBanner",
        "element": "Approve button",
        "handler": "src/components/ApproveBanner.tsx:44",
        "expected": "Approves commander-level authorization request",
        "backend": "POST /decisions/{id}/review (via markApproved)",
        "flag": "PASS"
    },

    # --- DECISION RAIL (OPERATOR VIEW) ---
    {
        "id": "DECRAIL-APPROVE-01",
        "view": "Ops Console / DecisionRail",
        "element": "✓ Approve button",
        "handler": "src/components/DecisionRail.tsx:383",
        "expected": "Submits approved review to backend, triggers maneuver demo if applicable",
        "backend": "POST /decisions/{id}/review {status: 'approved'}, WS reviews.approved",
        "flag": "PASS"
    },
    {
        "id": "DECRAIL-REJECT-01",
        "view": "Ops Console / DecisionRail",
        "element": "✕ Reject button",
        "handler": "src/components/DecisionRail.tsx:390",
        "expected": "Submits rejected review to backend",
        "backend": "POST /decisions/{id}/review {status: 'rejected'}, WS reviews.rejected",
        "flag": "PASS"
    },
    {
        "id": "DECRAIL-RECONSIDER-01",
        "view": "Ops Console / DecisionRail",
        "element": "Reconsider button",
        "handler": "src/components/DecisionRail.tsx:368",
        "expected": "Clears review status locally",
        "backend": "Local Zustand state clearDecisionStatus",
        "flag": "PASS"
    },
    {
        "id": "DECRAIL-TRACE-01",
        "view": "Ops Console / DecisionRail",
        "element": "View trace button",
        "handler": "src/components/DecisionRail.tsx:399",
        "expected": "Highlights related reasoning trace and scrolls ReasoningPanel",
        "backend": "Local Zustand state setActiveTraceRefId",
        "flag": "PASS"
    },

    # --- SYSTEM ACTIONS / ACTION LOG (OPS CONSOLE & BRIGADE) ---
    {
        "id": "ACTIONLOG-EXPAND-01",
        "view": "Ops Console & Brigade / ActionLog",
        "element": "Tool execution card expand/collapse row",
        "handler": "src/components/ActionLog.tsx:320",
        "expected": "Toggles card to show arguments, results, and latency",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "ACTIONLOG-VIEWTRACE-01",
        "view": "Ops Console & Brigade / ActionLog",
        "element": "View Trace icon button (↗)",
        "handler": "src/components/ActionLog.tsx:402",
        "expected": "Highlights associated trace item in ReasoningPanel",
        "backend": "Local Zustand state setActiveTraceRefId",
        "flag": "PASS"
    },

    # --- EVENT FEED (BOTTOM RAIL IN BRIGADE) ---
    {
        "id": "EVENTFEED-TAB-01",
        "view": "Brigade / EventFeed",
        "element": "RAW stream view tab",
        "handler": "src/components/EventFeed.tsx:427",
        "expected": "Displays raw timestamp-ordered signal stream",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "EVENTFEED-TAB-02",
        "view": "Brigade / EventFeed",
        "element": "FLOW narrative view tab",
        "handler": "src/components/EventFeed.tsx:440",
        "expected": "Displays correlated mission event narrative",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "EVENTFEED-AUTOFOCUS-01",
        "view": "Brigade / EventFeed",
        "element": "Auto-focus map toggle button",
        "handler": "src/components/EventFeed.tsx:455",
        "expected": "Toggles camera tracking of incoming signals on map/globe",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "EVENTFEED-ITEM-01",
        "view": "Brigade / EventFeed",
        "element": "Signal row expansion toggle button",
        "handler": "src/components/EventFeed.tsx:754",
        "expected": "Expands signal card to show raw payload and provenance",
        "backend": "None (UI state)",
        "flag": "PASS"
    },

    # --- TIMELINE & ACCORDIONS ---
    {
        "id": "COLLAPSIBLE-STACK-01",
        "view": "Brigade / CollapsibleStackSection",
        "element": "Accordion section header button",
        "handler": "src/components/CollapsibleStackSection.tsx:39",
        "expected": "Expands/collapses section (Timeline, System actions, etc.)",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "TIMELINE-DOT-01",
        "view": "Brigade / ScenarioTimeline",
        "element": "Timeline event node button",
        "handler": "src/components/ScenarioTimeline.tsx:158",
        "expected": "Selects event node to view operational details popup",
        "backend": "None (UI state)",
        "flag": "PASS"
    },

    # --- REASONING PANEL ---
    {
        "id": "REASONING-CLEAR-01",
        "view": "Ops Console & Brigade / ReasoningPanel",
        "element": "Clear reasoning stream button",
        "handler": "src/components/ReasoningPanel.tsx:85",
        "expected": "Clears streamed reasoning traces from event store",
        "backend": "Local Zustand state clearTraces",
        "flag": "PASS"
    },

    # --- STRESS MODE ---
    {
        "id": "STRESS-TOGGLE-01",
        "view": "Brigade / StressMode",
        "element": "Domain stress filter checkbox",
        "handler": "src/components/StressMode.tsx:108",
        "expected": "Toggles domain in blocked_domains stress list",
        "backend": "Local UI state",
        "flag": "PASS"
    },
    {
        "id": "STRESS-APPLY-01",
        "view": "Brigade / StressMode",
        "element": "Apply stress configuration button",
        "handler": "src/components/StressMode.tsx:118",
        "expected": "Sends blocked domain list to engine stress mode endpoint",
        "backend": "POST /stress",
        "flag": "PASS"
    },

    # --- ERROR BOUNDARY ---
    {
        "id": "ERRBOUNDARY-RETRY-01",
        "view": "Error Boundary",
        "element": "Try Again button",
        "handler": "src/components/ErrorBoundary.tsx:62",
        "expected": "Resets ErrorBoundary state to attempt re-rendering child view",
        "backend": "None (UI state)",
        "flag": "PASS"
    },
    {
        "id": "ERRBOUNDARY-RELOAD-01",
        "view": "Error Boundary",
        "element": "Reload Application button",
        "handler": "src/components/ErrorBoundary.tsx:78",
        "expected": "Reloads window",
        "backend": "None (Browser reload)",
        "flag": "PASS"
    },

    # --- OPS CONSOLE HEADER ---
    {
        "id": "OPS-HDR-01",
        "view": "Ops Console / Header",
        "element": "Nav link to Brigade View",
        "handler": "src/pages/Operator.tsx:26",
        "expected": "Navigates browser to /brigade (Brigade COP)",
        "backend": "None (Client-side / Browser navigation)",
        "flag": "PASS"
    },

    # --- STANDALONE HEADER COMPONENT ---
    {
        "id": "HEADER-TAB-01",
        "view": "Unmounted Component / Header",
        "element": "View mode tab buttons (Brigade / Operator)",
        "handler": "src/components/Header.tsx:30",
        "expected": "Updates store.view state",
        "backend": "Local Zustand state setView",
        "flag": "UNMOUNTED (Page uses custom header layouts with links)"
    },

    # --- ORBITAL TAKEOVER MODAL ---
    {
        "id": "TAKEOVER-CLOSE-01",
        "view": "Modal / OrbitalTakeover",
        "element": "Close modal button (✕)",
        "handler": "src/components/OrbitalTakeover.tsx:230",
        "expected": "Closes takeover modal",
        "backend": "None (UI state)",
        "flag": "PASS"
    },

    # --- DECISION DETAIL MODAL ---
    {
        "id": "DECDETAIL-CLOSE-01",
        "view": "Modal / DecisionDetail",
        "element": "Close detail modal button",
        "handler": "src/components/DecisionDetail.tsx:39",
        "expected": "Closes decision detail modal",
        "backend": "None (UI state)",
        "flag": "PASS"
    }
]

def generate_markdown():
    lines = [
        "# ORBIT Frontend Interactive Controls Audit & Inventory",
        "",
        "This document lists every interactive control across the ORBIT frontend, its handler location, expected behavior, involved backend/WS communication, and audit status.",
        "",
        "| ID | View / Panel | Element | Handler (file:line) | Expected Behavior | Backend Call / WS Event | Audit Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for item in INVENTORY:
        lines.append(
            f"| `{item['id']}` | {item['view']} | {item['element']} | [`{item['handler']}`](file:///{item['handler']}) | {item['expected']} | {item['backend']} | **{item['flag']}** |"
        )
    return "\n".join(lines) + "\n"

if __name__ == "__main__":
    content = generate_markdown()
    Path("docs/FRONTEND_AUDIT.md").write_text(content, encoding="utf-8")
    print(f"Generated docs/FRONTEND_AUDIT.md with {len(INVENTORY)} interactive controls.")
