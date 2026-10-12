import { useEffect, useState } from 'react'
import { useEventStore } from '../store/eventStore'
import type {
  Anomaly,
  Attribution,
  Decision,
  KBEntry,
  OrbitMessage,
  OrbitSocketState,
  OsintEmbeddingSnapshot,
  ReasoningTrace,
  Signal,
  UIEvent,
} from '../types/orbit'
import { getApiBaseUrl, getWsUrl } from '../lib/apiConfig'

export const MOCK_URL: string | null = null
const API_URL = getApiBaseUrl()
const DEFAULT_URL: string = getWsUrl()

const RECONNECT_BACKOFFS_MS = [500, 1500, 4000]
const FIXTURE_REPLAY_INTERVAL_MS = 3000

const FIXTURE_SIGNAL_LOCATIONS: Record<
  string,
  { lat: number; lng: number; label: string }
> = {
  'orbit-beat1-001': { lat: 21.3, lng: 142.8, label: 'LEO track over Western Pacific' },
  'orbit-beat1-002': { lat: 20.1, lng: 121.7, label: 'Luzon Strait' },
  'orbit-beat2-001': { lat: 13.5, lng: 144.8, label: 'Guam RF site' },
  'orbit-beat2-002': { lat: 13.5, lng: 144.8, label: 'Guam ground gateway' },
  'orbit-beat2-003': { lat: 18.5, lng: 121.0, label: 'Northern Luzon monitor' },
  'orbit-beat2-004': { lat: 15.0, lng: 145.6, label: 'Tinian launch corridor' },
  'orbit-beat47-001': { lat: 13.5, lng: 144.8, label: 'Guam SATCOM gateway' },
  'orbit-beat47-002': { lat: 14.9, lng: 132.1, label: 'ORBIT-LEO-07 relative motion frame' },
  'orbit-beat47-003': { lat: 14.5, lng: 133.8, label: 'ORBIT-LEO-07 downlink footprint' },
  'orbit-beat47-004': { lat: 13.5, lng: 144.8, label: 'Guam RF site' },
}

interface FixtureFile {
  events: UIEvent[]
}

const initialState: OrbitSocketState = {
  signals: [],
  anomalies: [],
  attributions: [],
  decisions: [],
  uiEvents: [],
  traces: [],
  isConnected: false,
  lastError: null,
}

const prependLimited = <T extends { id: string }>(
  items: T[],
  next: T,
  limit: number,
) => [next, ...items.filter((item) => item.id !== next.id)].slice(0, limit)

function normalizeMessage(value: unknown): OrbitMessage | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as {
    type?: unknown
    kind?: unknown
    data?: unknown
  }
  if (
    candidate.data &&
    typeof candidate.data === 'object' &&
    (candidate.data as Record<string, unknown>).type === 'decision_review'
  ) {
    return { type: 'review', data: candidate.data as never }
  }
  const discriminator =
    typeof candidate.type === 'string'
      ? candidate.type
      : typeof candidate.kind === 'string'
        ? candidate.kind
        : null
  if (!discriminator) return null
  if (
    ![
      'signal',
      'anomaly',
      'attribution',
      'decision',
      'ui_event',
      'trace',
      'embedding',
      'reset',
      'review',
    ].includes(discriminator)
  ) {
    return null
  }
  if (typeof candidate.data !== 'object' || candidate.data === null) return null
  return { type: discriminator, data: candidate.data } as OrbitMessage
}

function reduceMessage(
  state: OrbitSocketState,
  message: OrbitMessage,
): OrbitSocketState {
  const store = useEventStore.getState()
  switch (message.type) {
    case 'reset':
      store.reset()
      return {
        ...state,
        signals: [],
        anomalies: [],
        attributions: [],
        decisions: [],
        uiEvents: [],
        traces: [],
      }
    case 'review': {
      const rev = message.data
      if (rev && rev.decision_id) {
        store.setDecisionReviews({ [rev.decision_id]: rev })
      }
      return state
    }
    case 'signal':
      store.ingestSignal(message.data as Signal)
      return {
        ...state,
        signals: prependLimited<Signal>(state.signals, message.data as Signal, 50),
      }
    case 'anomaly':
      store.ingestAnomaly(message.data as Anomaly)
      return {
        ...state,
        anomalies: prependLimited<Anomaly>(state.anomalies, message.data as Anomaly, 20),
      }
    case 'attribution':
      store.ingestAttribution(message.data as Attribution)
      return {
        ...state,
        attributions: prependLimited<Attribution>(
          state.attributions,
          message.data as Attribution,
          20,
        ),
      }
    case 'decision': {
      const dec = message.data as Decision
      if (!dec || !dec.id || !dec.action) {
        return state
      }
      store.ingestDecision(dec)
      return {
        ...state,
        decisions: prependLimited<Decision>(state.decisions, dec, 20),
      }
    }
    case 'ui_event':
      store.ingestUIEvent(message.data as UIEvent)
      return {
        ...state,
        uiEvents: prependLimited<UIEvent>(state.uiEvents, message.data as UIEvent, 20),
      }
    case 'trace': {
      const trace = message.data as ReasoningTrace
      store.ingestTrace(trace)
      const key = trace.id || `${trace.ts}-${trace.message}`
      if (state.traces.some((t) => (t.id || `${t.ts}-${t.message}`) === key)) {
        return state
      }
      return {
        ...state,
        traces: [...state.traces, trace].slice(-500),
      }
    }
    case 'embedding':
      store.ingestEmbeddingSnapshot(message.data as OsintEmbeddingSnapshot)
      return state
  }
}

class OrbitSocketManager {
  private socket: WebSocket | null = null
  private state: OrbitSocketState = initialState
  private listeners = new Set<(state: OrbitSocketState) => void>()
  private failureCount = 0
  private reconnectTimer: number | null = null
  private fixtureTimer: number | null = null
  private disconnectTimer: number | null = null
  private kbLoaded = false
  private url: string | null = DEFAULT_URL
  private isStarted = false

  constructor() {
    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', () => {
        this.disconnect()
      })
    }
  }

  public getState(): OrbitSocketState {
    return this.state
  }

  public subscribe(
    listener: (state: OrbitSocketState) => void,
    url: string | null = DEFAULT_URL,
  ): () => void {
    if (this.disconnectTimer !== null) {
      clearTimeout(this.disconnectTimer)
      this.disconnectTimer = null
    }
    this.listeners.add(listener)
    if (url) this.url = url
    if (!this.isStarted) {
      this.isStarted = true
      this.initKB()
      this.connect()
    }
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) {
        this.disconnectTimer = window.setTimeout(() => {
          this.disconnect()
        }, 5000)
      }
    }
  }

  public disconnect() {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.fixtureTimer !== null) {
      clearTimeout(this.fixtureTimer)
      this.fixtureTimer = null
    }
    if (this.socket) {
      try {
        this.socket.close()
      } catch {
        /* ignored */
      }
      this.socket = null
    }
    this.isStarted = false
    this.updateState({ isConnected: false })
  }

  private updateState(updater: Partial<OrbitSocketState> | ((prev: OrbitSocketState) => OrbitSocketState)) {
    if (typeof updater === 'function') {
      this.state = updater(this.state)
    } else {
      this.state = { ...this.state, ...updater }
    }
    for (const listener of this.listeners) {
      listener(this.state)
    }
  }

  private initKB() {
    if (this.kbLoaded) return
    this.kbLoaded = true
    void fetch(`${API_URL}/kb`)
      .then((r) => r.json())
      .then((data: { entries: KBEntry[] }) => {
        useEventStore.getState().setKB(data.entries)
      })
      .catch(() => {
        /* non-fatal */
      })
  }

  private startFixtureMode() {
    useEventStore.getState().setConnection('fixture')
    void fetch(`${API_URL}/fixture/ui_events`)
      .then((r) => r.json())
      .then((data: FixtureFile) => {
        let i = 0
        const tick = () => {
          if (this.socket && this.socket.readyState === WebSocket.OPEN) return
          const event = data.events[i % data.events.length]
          useEventStore.getState().ingestUIEvent({
            ...event,
            timestamp: new Date().toISOString(),
          })
          for (const sid of event.source_signal_ids ?? []) {
            const loc = FIXTURE_SIGNAL_LOCATIONS[sid]
            if (loc) {
              useEventStore.getState().ingestSignal({
                id: sid,
                ts: new Date().toISOString(),
                domain: 'osint',
                source: 'fixture',
                realism: 'mock_operational',
                confidence: event.confidence,
                location: loc,
                payload: { event_type: 'fixture', summary: event.title },
                provenance: { source_id: 'fixture' },
              } as Signal)
            }
          }
          i += 1
          this.fixtureTimer = window.setTimeout(tick, FIXTURE_REPLAY_INTERVAL_MS)
        }
        tick()
      })
      .catch(() => useEventStore.getState().setConnection('offline'))
  }

  private scheduleReconnectOrFallback() {
    if (this.reconnectTimer !== null) return
    const failures = this.failureCount
    if (failures >= RECONNECT_BACKOFFS_MS.length) {
      this.startFixtureMode()
      return
    }
    const delay = RECONNECT_BACKOFFS_MS[failures]
    this.failureCount = failures + 1
    useEventStore.getState().setConnection('connecting')
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private connect() {
    if (!this.url) {
      this.startFixtureMode()
      return
    }

    if (this.socket) {
      if (
        this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING
      ) {
        return
      }
      try {
        this.socket.close()
      } catch {
        /* ignored */
      }
      this.socket = null
    }

    useEventStore.getState().setConnection('connecting')

    try {
      const socket = new WebSocket(this.url)
      this.socket = socket

      socket.addEventListener('open', () => {
        if (this.socket !== socket) return
        this.failureCount = 0
        if (this.fixtureTimer !== null) {
          clearTimeout(this.fixtureTimer)
          this.fixtureTimer = null
        }
        useEventStore.getState().setConnection('live')
        this.updateState({ isConnected: true, lastError: null })
      })

      socket.addEventListener('close', () => {
        if (this.socket !== socket) return
        this.updateState({ isConnected: false })
        this.scheduleReconnectOrFallback()
      })

      socket.addEventListener('error', () => {
        if (this.socket !== socket) return
        this.updateState({ lastError: 'ORBIT socket error' })
      })

      socket.addEventListener('message', (event: MessageEvent<string>) => {
        if (this.socket !== socket) return
        try {
          const parsed: unknown = JSON.parse(event.data)
          const message = normalizeMessage(parsed)
          if (!message) return
          this.updateState((current) => reduceMessage(current, message))
        } catch {
          this.updateState({ lastError: 'Invalid ORBIT socket payload' })
        }
      })
    } catch {
      this.scheduleReconnectOrFallback()
    }
  }
}

const socketManager = new OrbitSocketManager()

export function useOrbitSocket(url: string | null = DEFAULT_URL): OrbitSocketState {
  const [state, setState] = useState<OrbitSocketState>(() => socketManager.getState())

  useEffect(() => {
    return socketManager.subscribe(setState, url)
  }, [url])

  return state
}
