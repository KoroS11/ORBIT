import { useEffect, useRef } from 'react'
import { useEventStore } from '../store/eventStore'
import type { ReasoningTrace, TraceStage } from '../types/orbit'

const STAGE_COLORS: Record<TraceStage, string> = {
  fusion: 'var(--cyan)',
  attrib_primary: 'var(--green)',
  attrib_redteam: 'var(--red)',
  attrib_reconcile: 'var(--olive)',
  decide: 'var(--violet)',
  tools: 'var(--amber)',
  stress: 'var(--red)',
}

const STAGE_LABEL: Record<TraceStage, string> = {
  fusion: 'fusion',
  attrib_primary: 'attrib.primary',
  attrib_redteam: 'attrib.redteam',
  attrib_reconcile: 'attrib.reconcile',
  decide: 'decide',
  tools: 'tools',
  stress: 'stress',
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return '--:--:--'
  }
}

interface Props {
  /** When true, the panel renders compact (Brigade footer); otherwise full-height. */
  compact?: boolean
}

export function ReasoningPanel({ compact = false }: Props) {
  const traces = useEventStore((s) => s.traces)
  const activeTraceRefId = useEventStore((s) => s.activeTraceRefId)
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const el = ref.current
    if (el && !activeTraceRefId) {
      el.scrollTop = el.scrollHeight
    }
  }, [traces.length, activeTraceRefId])

  useEffect(() => {
    if (!activeTraceRefId || !ref.current) return
    const matched = ref.current.querySelector(
      `[data-ref-id="${activeTraceRefId}"]`,
    )
    if (matched) {
      matched.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [activeTraceRefId])

  const clear = () => {
    useEventStore.getState().reset()
    const apiUrl = import.meta.env.VITE_ORBIT_API_URL ?? 'http://127.0.0.1:8000'
    void fetch(`${apiUrl}/reset`, { method: 'POST' }).catch(() => {})
  }

  return (
    <section
      className={`reasoning-panel${compact ? ' reasoning-panel--compact' : ''}`}
      aria-labelledby="reasoning-title"
    >
      <div className="panel__header">
        <h2 id="reasoning-title">Reasoning trace</h2>
        <div className="reasoning-panel__head-actions">
          <span>{traces.length} lines</span>
          <button
            type="button"
            className="reasoning-panel__clear"
            onClick={clear}
            title="Reset all engine state — traces, anomalies, decisions, embeddings, action log, approvals"
          >
            reset
          </button>
        </div>
      </div>
      <div className="reasoning-panel__stream" ref={ref}>
        {traces.length === 0 ? (
          <div className="reasoning-panel__empty">
            waiting for engine output…
          </div>
        ) : (
          traces.map((trace) => {
            const isHighlighted =
              Boolean(activeTraceRefId) &&
              (trace.ref_id === activeTraceRefId ||
                trace.payload?.attribution_id === activeTraceRefId)
            return (
              <TraceLine
                key={trace.id}
                trace={trace}
                isHighlighted={isHighlighted}
              />
            )
          })
        )}
      </div>
    </section>
  )
}

function TraceLine({
  trace,
  isHighlighted = false,
}: {
  trace: ReasoningTrace
  isHighlighted?: boolean
}) {
  const color = STAGE_COLORS[trace.stage] ?? 'var(--text-muted)'
  return (
    <div
      id={`trace-${trace.id}`}
      data-ref-id={trace.ref_id ?? undefined}
      className={`reasoning-line reasoning-line--${trace.level}${
        isHighlighted ? ' reasoning-line--highlighted' : ''
      }`}
    >
      <span className="reasoning-line__time">{formatTime(trace.ts)}</span>
      <span className="reasoning-line__stage" style={{ color }}>
        [{STAGE_LABEL[trace.stage] ?? trace.stage}]
      </span>
      <span className="reasoning-line__msg">{trace.message}</span>
    </div>
  )
}
