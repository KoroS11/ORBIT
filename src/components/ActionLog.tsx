import { useMemo, useState } from 'react'
import { useEventStore } from '../store/eventStore'
import type { ReasoningTrace } from '../types/orbit'

interface Props {
  /** When true, the panel renders compact for a sidebar slot. */
  compact?: boolean
  /** Maximum rows to render; oldest are pushed off the top. */
  limit?: number
}

const TOOL_GLYPH: Record<string, string> = {
  'kb.lookup': '◆',
  'orbit.compute_close_approach': '⊙',
  'orbit.simulate_maneuver': '↗',
  'request.draft': '✎',
  'routing.validate': '✓',
}

const TOOL_LABEL: Record<string, string> = {
  'kb.lookup': 'kb.lookup',
  'orbit.compute_close_approach': 'orbit.compute_close_approach',
  'orbit.simulate_maneuver': 'orbit.simulate_maneuver',
  'request.draft': 'request.draft',
  'routing.validate': 'routing.validate',
}

const TOOL_DESCRIPTIONS: Record<string, string> = {
  'kb.lookup':
    'Query doctrine, actor capabilities, and threat signatures from the knowledge base.',
  'request.draft':
    'Draft structured authorization request packet with justification and parameters.',
  'routing.validate':
    'Validate operational action against escalation rules and authority requirements.',
  'orbit.compute_close_approach':
    'Compute orbital relative motion and time of closest approach via SGP4.',
  'orbit.simulate_maneuver':
    'Simulate Clohessy-Wiltshire burn to evaluate post-maneuver miss distance.',
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

function formatDuration(ms: number | undefined): string | null {
  if (ms === undefined || ms === null || isNaN(ms)) return null
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${ms.toFixed(1)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function summariseResult(trace: ReasoningTrace): string {
  const tool = (trace.payload?.tool ?? '') as string
  const result = (trace.payload?.result ?? {}) as Record<string, unknown>
  if (result.error) return `error: ${String(result.error)}`
  switch (tool) {
    case 'kb.lookup': {
      const count = (result.count ?? 0) as number
      return `${count} KB ${count === 1 ? 'entry' : 'entries'}`
    }
    case 'orbit.compute_close_approach': {
      const km = result.closest_approach_km
      const t = result.t_closest as string | undefined
      const source = result.source as string | undefined
      const time = t
        ? new Date(t).toLocaleTimeString('en-US', {
            hour12: false,
            timeZone: 'UTC',
          }) + ' UTC'
        : '—'
      const label = source ? ` [${source}]` : ''
      return km !== undefined ? `${km} km @ ${time}${label}` : 'computed'
    }
    case 'orbit.simulate_maneuver': {
      const pre = result.pre_miss_km
      const post = result.post_miss_km
      const dv = result.dv_m_s
      return `${pre} → ${post} km · Δv ${dv} m/s`
    }
    case 'request.draft': {
      const packet = (result.request_packet ?? {}) as Record<string, unknown>
      return `packet to ${packet.to ?? 'CJFSCC'}`
    }
    case 'routing.validate': {
      return result.valid ? 'valid' : `invalid · ${String(result.reason ?? '')}`
    }
    default:
      return trace.message
  }
}

function summariseArgs(trace: ReasoningTrace): string {
  const tool = (trace.payload?.tool ?? '') as string
  const args = (trace.payload?.args ?? {}) as Record<string, unknown>
  switch (tool) {
    case 'kb.lookup':
      return (
        [args.actor, args.capability_type, args.scenario_signal_id]
          .filter(Boolean)
          .join(' · ') || 'all entries'
      )
    case 'orbit.compute_close_approach':
      return `${args.sat_a} vs ${args.sat_b}`
    case 'orbit.simulate_maneuver':
      return `${args.sat} vs ${args.against ?? '?'}`
    case 'request.draft':
      return `${args.actor} (conf ${args.confidence})`
    case 'routing.validate':
      return `${args.action} → ${args.authority}`
    default:
      return Object.keys(args).length ? JSON.stringify(args) : ''
  }
}

interface ToolRun {
  runId: string
  refId: string | null
  timestamp: string
  calls: ReasoningTrace[]
  elapsedMs: number | null
}

export function ActionLog({ compact = false, limit = 40 }: Props) {
  const traces = useEventStore((s) => s.traces)
  const decisionsById = useEventStore((s) => s.decisionsById)
  const setActiveTraceRefId = useEventStore((s) => s.setActiveTraceRefId)

  // Track expanded card IDs
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({})

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  // Filter traces that represent tool executions
  const actions = useMemo(
    () => traces.filter((t) => t.stage === 'tools' || Boolean(t.payload?.tool)),
    [traces],
  )

  // Group tool calls by run (same decision ref_id or timestamp cluster)
  const runs = useMemo(() => {
    const sliced = actions.slice(-limit)
    if (sliced.length === 0) return []

    const groups: ToolRun[] = []
    let currentRun: ToolRun | null = null

    sliced.forEach((trace) => {
      const refId = trace.ref_id ?? null
      const traceTime = new Date(trace.ts).getTime()

      // Determine if this belongs to current run
      const belongsToCurrent =
        currentRun !== null &&
        ((refId !== null && currentRun.refId === refId) ||
          (refId === null &&
            currentRun.refId === null &&
            Math.abs(traceTime - new Date(currentRun.timestamp).getTime()) < 5000))

      if (belongsToCurrent && currentRun) {
        currentRun.calls.push(trace)
      } else {
        if (currentRun) {
          groups.push(currentRun)
        }
        currentRun = {
          runId: refId ? `run-${refId}` : `run-${trace.id}`,
          refId,
          timestamp: trace.ts,
          calls: [trace],
          elapsedMs: null,
        }
      }
    })

    if (currentRun) {
      groups.push(currentRun)
    }

    // Compute elapsed time for each run from real backend duration data and timestamps
    groups.forEach((run) => {
      let sumDurations = 0
      let hasDurations = false

      run.calls.forEach((c) => {
        const d = c.payload?.duration_ms
        if (typeof d === 'number') {
          sumDurations += d
          hasDurations = true
        }
      })

      const times = run.calls.map((c) => new Date(c.ts).getTime()).filter((t) => !isNaN(t))
      const timeSpan = times.length > 1 ? Math.max(...times) - Math.min(...times) : 0

      if (hasDurations) {
        run.elapsedMs = Math.max(sumDurations, timeSpan)
      } else if (times.length > 1 && timeSpan > 0) {
        run.elapsedMs = timeSpan
      } else {
        run.elapsedMs = null
      }
    })

    // Return newest runs first
    return groups.reverse()
  }, [actions, limit])

  return (
    <section
      className={`action-log${compact ? ' action-log--compact' : ''}`}
      aria-labelledby="action-log-title"
    >
      <div className="panel__header">
        <h2 id="action-log-title">System actions</h2>
        <span>
          {runs.length} {runs.length === 1 ? 'run' : 'runs'} · {actions.length} calls
        </span>
      </div>

      <div className="action-log__stream">
        {runs.length === 0 ? (
          <div className="action-log__empty">
            no tool calls yet — run a scenario to inspect automated tool dispatches
          </div>
        ) : (
          <div className="action-log__runs">
            {runs.map((run) => {
              const assocDecision = run.refId ? decisionsById[run.refId] : null
              const runTitle = assocDecision
                ? `Run: ${assocDecision.action}`
                : run.refId
                ? `Run for ${run.refId}`
                : `Run @ ${formatTime(run.timestamp)}`

              const elapsedStr = formatDuration(run.elapsedMs ?? undefined)

              return (
                <section key={run.runId} className="action-log__run-group">
                  {/* Run Header with Call Count and Elapsed Time */}
                  <header className="action-log__run-header">
                    <div className="action-log__run-title-group">
                      <span className="action-log__run-dot" aria-hidden="true" />
                      <span className="action-log__run-title">{runTitle}</span>
                      {run.refId ? (
                        <span className="action-log__run-ref-tag">
                          {run.refId}
                        </span>
                      ) : null}
                    </div>
                    <div className="action-log__run-stats">
                      <span className="action-log__run-count">
                        {run.calls.length}{' '}
                        {run.calls.length === 1 ? 'call' : 'calls'}
                      </span>
                      {elapsedStr ? (
                        <span className="action-log__run-elapsed">
                          ⏱ {elapsedStr} elapsed
                        </span>
                      ) : null}
                    </div>
                  </header>

                  {/* Expandable Cards for Each Tool Call */}
                  <div className="action-log__cards">
                    {run.calls.map((trace) => {
                      const tool = (trace.payload?.tool ?? '') as string
                      const glyph = TOOL_GLYPH[tool] ?? '◆'
                      const label = TOOL_LABEL[tool] ?? tool
                      const isExpanded = Boolean(expandedIds[trace.id])
                      const staticDesc =
                        TOOL_DESCRIPTIONS[tool] ??
                        'Autonomous decision-support tool execution.'

                      // Status from backend payload status or derived from result
                      const result = (trace.payload?.result ?? {}) as Record<
                        string,
                        unknown
                      >
                      const backendStatus = trace.payload?.status as
                        | string
                        | undefined
                      const status =
                        backendStatus ??
                        (result.error || trace.level === 'warn'
                          ? 'error'
                          : result.valid === false
                          ? 'invalid'
                          : 'success')

                      const durationMs = trace.payload?.duration_ms as
                        | number
                        | undefined
                      const durationStr = formatDuration(durationMs)

                      const inputSummary = summariseArgs(trace)
                      const outputSummary = summariseResult(trace)

                      return (
                        <article
                          key={trace.id}
                          className={`action-card action-card--${status}${
                            isExpanded ? ' action-card--expanded' : ''
                          }`}
                        >
                          {/* Card Header (Clickable to Expand/Collapse) */}
                          <div
                            className="action-card__header"
                            onClick={() => toggleExpand(trace.id)}
                            role="button"
                            tabIndex={0}
                            aria-expanded={isExpanded}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault()
                                toggleExpand(trace.id)
                              }
                            }}
                          >
                            <div className="action-card__header-left">
                              <span
                                className="action-card__glyph"
                                aria-hidden="true"
                              >
                                {glyph}
                              </span>
                              <span className="action-card__tool-name">
                                {label}
                              </span>
                              <span className="action-card__time">
                                {formatTime(trace.ts)}
                              </span>
                            </div>

                            <div className="action-card__header-right">
                              {/* Duration badge: only shown if duration is available */}
                              {durationStr ? (
                                <span className="action-card__duration">
                                  {durationStr}
                                </span>
                              ) : null}

                              {/* Status badge */}
                              <span
                                className={`action-card__status action-card__status--${status}`}
                              >
                                {status.toUpperCase()}
                              </span>

                              <span
                                className="action-card__chevron"
                                aria-hidden="true"
                              >
                                {isExpanded ? '▾' : '▸'}
                              </span>
                            </div>
                          </div>

                          {/* Quick Summary Row */}
                          <div className="action-card__summary-row">
                            <span className="action-card__io-chip action-card__io-chip--in">
                              IN
                            </span>
                            <span className="action-card__io-text">
                              {inputSummary || '—'}
                            </span>
                            <span className="action-card__io-arrow">→</span>
                            <span className="action-card__io-chip action-card__io-chip--out">
                              OUT
                            </span>
                            <span className="action-card__io-text action-card__io-text--out">
                              {outputSummary}
                            </span>
                          </div>

                          {/* Expanded Card Details */}
                          {isExpanded ? (
                            <div className="action-card__body">
                              {/* Static one-line description per tool type */}
                              <p className="action-card__desc">{staticDesc}</p>

                              {/* Associated Decision */}
                              {trace.ref_id ? (
                                <div className="action-card__decision-row">
                                  <span className="action-card__meta-label">
                                    Belongs to decision:
                                  </span>
                                  <button
                                    type="button"
                                    className="action-card__decision-link"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      if (trace.ref_id) {
                                        setActiveTraceRefId(trace.ref_id)
                                      }
                                    }}
                                  >
                                    {trace.ref_id}
                                    {assocDecision
                                      ? ` (${assocDecision.action} · ${assocDecision.target})`
                                      : ''}
                                  </button>
                                </div>
                              ) : null}

                              {/* Detailed input arguments */}
                              {trace.payload?.args &&
                              Object.keys(trace.payload.args).length > 0 ? (
                                <div className="action-card__data-block">
                                  <span className="action-card__data-title">
                                    Input Parameters
                                  </span>
                                  <pre className="action-card__json">
                                    {JSON.stringify(trace.payload.args, null, 2)}
                                  </pre>
                                </div>
                              ) : null}

                              {/* Detailed output result */}
                              {trace.payload?.result &&
                              Object.keys(trace.payload.result).length > 0 ? (
                                <div className="action-card__data-block">
                                  <span className="action-card__data-title">
                                    Output Result
                                  </span>
                                  <pre className="action-card__json">
                                    {JSON.stringify(trace.payload.result, null, 2)}
                                  </pre>
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                        </article>
                      )
                    })}
                  </div>
                </section>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}
