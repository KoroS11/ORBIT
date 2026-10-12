import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { getApiBaseUrl } from "../lib/apiConfig";
import type {
  Anomaly,
  Attribution,
  ConnectionStatus,
  Decision,
  DecisionReview,
  KBEntry,
  OsintEmbeddingSnapshot,
  ReasoningTrace,
  Signal,
  UIEvent,
  ViewMode,
} from "../types/orbit";

const RING_BUFFER = 200;
const TRACE_BUFFER = 500;

export type ManeuverDemo = {
  /** Decision that triggered this animation. */
  decisionId: string;
  /** Wall-clock ms when the animation started (Date.now()). */
  startedAt: number;
  /** Total animation duration in ms — Cesium uses this to schedule the
   *  end of the visualization. */
  durationMs: number;
  /** Pre-burn miss distance (km) pulled from the engine's request packet. */
  preMissKm: number;
  /** Post-burn miss distance (km) — what the operator's accept achieves. */
  postMissKm: number;
  /** Δv applied (m/s). Drives the apparent magnitude of the burn vector. */
  dvMs: number;
  /** Friendly satellite name (matches an entry in the n2yo catalog). */
  friendlyLabel?: string;
  /** Hostile satellite name (matches an entry in the n2yo catalog). */
  hostileLabel?: string;
  /** Which Cesium animation runs.
   *  - `evasion`: friendly executes a plane-change burn off a shared orbit
   *    (active_defense_escort, active_defense_counterattack, default)
   *  - `strike`: friendly fires a kinetic kill vehicle on a Bezier arc
   *    that intercepts the hostile (orbital_strike_request, also fits
   *    active_defense_counterattack on offensive scenarios)
   *  - `interdiction`: friendly emits a jamming beam that breaks the
   *    hostile's link (space_link_interdiction_request, sda_tasking) */
  demoType?: 'evasion' | 'strike' | 'interdiction';
};

function pushBounded<T extends { id: string }>(buffer: T[], item: T): T[] {
  const existingIdx = buffer.findIndex((existing) => existing.id === item.id);
  if (existingIdx !== -1) {
    const next = [...buffer];
    next[existingIdx] = item;
    return next;
  }
  const next = [item, ...buffer];
  if (next.length > RING_BUFFER) next.length = RING_BUFFER;
  return next;
}

function appendBounded<T extends { id?: string; ts?: string; message?: string }>(
  buffer: T[],
  item: T,
  cap: number,
): T[] {
  const key = item.id || `${item.ts || ''}-${item.message || ''}`;
  if (
    buffer.some((existing) => {
      const existingKey = existing.id || `${existing.ts || ''}-${existing.message || ''}`;
      return existingKey === key;
    })
  ) {
    return buffer;
  }
  const next = [...buffer, item];
  if (next.length > cap) next.splice(0, next.length - cap);
  return next;
}

interface EventState {
  // Per-kind ring buffers, newest first.
  signals: Signal[];
  anomalies: Anomaly[];
  attributions: Attribution[];
  decisions: Decision[];
  uiEvents: UIEvent[];

  // Reasoning trace buffer, oldest first (terminal-style: latest at bottom).
  traces: ReasoningTrace[];

  // Latest OSINT semantic-clustering snapshot (replaces wholesale on each
  // arrival — the backend sends the full sliding window every time).
  embeddingSnapshot: OsintEmbeddingSnapshot | null;

  // Lookup tables for cross-event linking.
  signalsById: Record<string, Signal>;
  attributionsById: Record<string, Attribution>;
  decisionsById: Record<string, Decision>;

  // Connection + view state.
  connection: ConnectionStatus;
  view: ViewMode;
  selectedEventId: string | null;
  pendingApproval: UIEvent | null;
  approvedEventIds: Set<string>;
  /** Decision ids the operator accepted in the left-rail action panel. */
  acceptedDecisionIds: Set<string>;
  /** Decision ids the operator denied in the left-rail action panel. */
  deferredDecisionIds: Set<string>;
  /** Persisted review records for decisions (approved/rejected with actor + timestamp). */
  decisionReviews: Record<string, DecisionReview>;
  /** Currently highlighted trace refId (e.g. decision id). */
  activeTraceRefId: string | null;
  /** Live maneuver-demo overlay state. When set, the Cesium globe runs
   *  the accept-action visualization (hostile approach → friendly burn
   *  → miss). Cleared automatically when the animation finishes. */
  maneuverDemo: ManeuverDemo | null;
  takeoverEvent: UIEvent | null;

  // Knowledge base resolved by id (loaded once via GET /kb).
  kb: Record<string, KBEntry>;

  // Mutators.
  ingestSignal: (signal: Signal) => void;
  ingestAnomaly: (anomaly: Anomaly) => void;
  ingestAttribution: (attribution: Attribution) => void;
  ingestDecision: (decision: Decision) => void;
  ingestUIEvent: (event: UIEvent) => void;
  ingestTrace: (trace: ReasoningTrace) => void;
  ingestEmbeddingSnapshot: (snapshot: OsintEmbeddingSnapshot) => void;

  setConnection: (status: ConnectionStatus) => void;
  setView: (view: ViewMode) => void;
  selectEvent: (id: string | null) => void;
  dismissApproval: () => void;
  markApproved: (id: string) => void;
  acceptDecision: (id: string) => void;
  deferDecision: (id: string) => void;
  clearDecisionStatus: (id: string) => void;
  reviewDecision: (id: string, status: "approved" | "rejected", actor?: string) => void;
  setDecisionReviews: (reviews: Record<string, DecisionReview>) => void;
  setActiveTraceRefId: (refId: string | null) => void;
  startManeuverDemo: (demo: ManeuverDemo) => void;
  endManeuverDemo: () => void;
  openTakeover: (event: UIEvent) => void;
  closeTakeover: () => void;
  setKB: (entries: KBEntry[]) => void;
  reset: () => void;
}

const initialState = (): Omit<
  EventState,
  | "ingestSignal"
  | "ingestAnomaly"
  | "ingestAttribution"
  | "ingestDecision"
  | "ingestUIEvent"
  | "ingestTrace"
  | "ingestEmbeddingSnapshot"
  | "setConnection"
  | "setView"
  | "selectEvent"
  | "dismissApproval"
  | "markApproved"
  | "acceptDecision"
  | "deferDecision"
  | "clearDecisionStatus"
  | "reviewDecision"
  | "setDecisionReviews"
  | "setActiveTraceRefId"
  | "startManeuverDemo"
  | "endManeuverDemo"
  | "openTakeover"
  | "closeTakeover"
  | "setKB"
  | "reset"
> => ({
  signals: [],
  anomalies: [],
  attributions: [],
  decisions: [],
  uiEvents: [],
  traces: [],
  embeddingSnapshot: null,
  signalsById: {},
  attributionsById: {},
  decisionsById: {},
  connection: "connecting",
  view: "brigade",
  selectedEventId: null,
  pendingApproval: null,
  approvedEventIds: new Set(),
  acceptedDecisionIds: new Set(),
  deferredDecisionIds: new Set(),
  decisionReviews: {},
  activeTraceRefId: null,
  maneuverDemo: null,
  takeoverEvent: null,
  kb: {},
});

export const useEventStore = create<EventState>()(
  persist(
    (set) => ({
      ...initialState(),

      ingestSignal: (signal) =>
    set((state) => ({
      signals: pushBounded(state.signals, signal),
      signalsById: { ...state.signalsById, [signal.id]: signal },
    })),

  ingestAnomaly: (anomaly) =>
    set((state) => ({
      anomalies: pushBounded(state.anomalies, anomaly),
    })),

  ingestAttribution: (attribution) =>
    set((state) => ({
      attributions: pushBounded(state.attributions, attribution),
      attributionsById: {
        ...state.attributionsById,
        [attribution.id]: attribution,
      },
    })),

  ingestDecision: (decision) => {
    if (!decision || !decision.id || !decision.action) {
      const candidate = decision as unknown as DecisionReview & { type?: string }
      if (
        candidate &&
        candidate.decision_id &&
        (candidate.status === "approved" || candidate.status === "rejected")
      ) {
        useEventStore
          .getState()
          .setDecisionReviews({ [candidate.decision_id]: candidate });
      }
      return;
    }
    set((state) => ({
      decisions: pushBounded(state.decisions, decision),
      decisionsById: { ...state.decisionsById, [decision.id]: decision },
    }));
  },

  ingestTrace: (trace) =>
    set((state) => ({
      traces: appendBounded(state.traces, trace, TRACE_BUFFER),
    })),

  ingestEmbeddingSnapshot: (snapshot) =>
    set({ embeddingSnapshot: snapshot }),

  ingestUIEvent: (event) =>
    set((state) => {
      const next = {
        uiEvents: pushBounded(state.uiEvents, event),
      } as Partial<EventState>;
      // Auto-pop the approve banner when a recommendation arrives, unless
      // this exact event has already been dismissed/approved.
      if (
        event.type === "recommendation_created" &&
        event.recommendation &&
        !state.approvedEventIds.has(event.id)
      ) {
        next.pendingApproval = event;
      }
      return next;
    }),

  setConnection: (connection) => set({ connection }),
  setView: (view) => set({ view }),
  selectEvent: (selectedEventId) => set({ selectedEventId }),
  dismissApproval: () => set({ pendingApproval: null }),
  markApproved: (id) =>
    set((state) => {
      const approvedEventIds = new Set(state.approvedEventIds);
      approvedEventIds.add(id);
      return {
        approvedEventIds,
        pendingApproval:
          state.pendingApproval?.id === id ? null : state.pendingApproval,
      };
    }),
  acceptDecision: (id) =>
    set((state) => {
      const acceptedDecisionIds = new Set(state.acceptedDecisionIds);
      acceptedDecisionIds.add(id);
      const deferredDecisionIds = new Set(state.deferredDecisionIds);
      deferredDecisionIds.delete(id);
      return { acceptedDecisionIds, deferredDecisionIds };
    }),
  deferDecision: (id) =>
    set((state) => {
      const deferredDecisionIds = new Set(state.deferredDecisionIds);
      deferredDecisionIds.add(id);
      const acceptedDecisionIds = new Set(state.acceptedDecisionIds);
      acceptedDecisionIds.delete(id);
      return { deferredDecisionIds, acceptedDecisionIds };
    }),
  clearDecisionStatus: (id) =>
    set((state) => {
      const acceptedDecisionIds = new Set(state.acceptedDecisionIds);
      const deferredDecisionIds = new Set(state.deferredDecisionIds);
      acceptedDecisionIds.delete(id);
      deferredDecisionIds.delete(id);
      const decisionReviews = { ...state.decisionReviews };
      delete decisionReviews[id];
      return { acceptedDecisionIds, deferredDecisionIds, decisionReviews };
    }),
  reviewDecision: (id, status, actor = "OPERATOR-01") => {
    const timestamp = new Date().toISOString();
    const review: DecisionReview = {
      decision_id: id,
      status,
      actor,
      timestamp,
    };
    set((state) => {
      const nextReviews = { ...state.decisionReviews, [id]: review };
      const acceptedDecisionIds = new Set(state.acceptedDecisionIds);
      const deferredDecisionIds = new Set(state.deferredDecisionIds);
      if (status === "approved") {
        acceptedDecisionIds.add(id);
        deferredDecisionIds.delete(id);
      } else {
        deferredDecisionIds.add(id);
        acceptedDecisionIds.delete(id);
      }
      return {
        decisionReviews: nextReviews,
        acceptedDecisionIds,
        deferredDecisionIds,
      };
    });
    const apiUrl = getApiBaseUrl();
    void fetch(`${apiUrl}/decisions/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, actor, timestamp }),
    }).catch(() => {});
  },
  setDecisionReviews: (reviews) =>
    set((state) => ({
      decisionReviews: { ...state.decisionReviews, ...reviews },
    })),
  setActiveTraceRefId: (activeTraceRefId) => set({ activeTraceRefId }),
  startManeuverDemo: (demo) => set({ maneuverDemo: demo }),
  endManeuverDemo: () => set({ maneuverDemo: null }),
  openTakeover: (event) => set({ takeoverEvent: event }),
  closeTakeover: () => set({ takeoverEvent: null }),
      setKB: (entries) =>
        set({
          kb: Object.fromEntries(entries.map((e) => [e.id, e])),
        }),
      reset: () => set(initialState()),
    }),
    {
      // Survives full-page navigations (the Brigade ↔ Operator header link
      // is a regular <a href>, so the JS state would otherwise reset on
      // every tab switch). Persisting the engine event ring buffers means
      // the reasoning panel and event timeline keep their history when
      // the user moves between pages.
      name: "orbit-event-store",
      version: 2,
      storage: createJSONStorage(() => sessionStorage),
      // Skip live UI/connection state and the Set (Sets don't survive
      // JSON.stringify cleanly). KB is fetched fresh on each mount.
      partialize: (state) => ({
        signals: state.signals,
        anomalies: state.anomalies,
        attributions: state.attributions,
        decisions: state.decisions,
        decisionReviews: state.decisionReviews,
        uiEvents: state.uiEvents,
        traces: state.traces,
        embeddingSnapshot: state.embeddingSnapshot,
        signalsById: state.signalsById,
        attributionsById: state.attributionsById,
        decisionsById: state.decisionsById,
      }),
    },
  ),
);
