import { useMemo } from 'react'
import { useEventStore, type ManeuverDemo } from '../store/eventStore'
import type { Decision, ReasoningTrace } from '../types/orbit'

const ACTION_LABELS: Record<string, string> = {
  active_defense_escort: 'Active defense escort',
  active_defense_counterattack: 'Active defense counterattack',
  orbital_strike_request: 'Orbital strike request',
  terrestrial_strike_request: 'Terrestrial strike request',
  space_link_interdiction_request: 'Space-link interdiction',
  sda_tasking: 'SDA tasking',
  threat_warning: 'Threat warning',
  passive_defense: 'Passive defense',
}

function formatAction(action: string): string {
  return (
    ACTION_LABELS[action] ??
    action
      .split(/[_\s]+/)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  )
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    return (
      d.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }) + ' UTC'
    )
  } catch {
    return ts
  }
}

const actionToDemoType = (action: string): ManeuverDemo['demoType'] => {
  if (
    action === 'orbital_strike_request' ||
    action === 'active_defense_counterattack'
  ) {
    return 'strike'
  }
  if (action === 'space_link_interdiction_request') {
    return 'interdiction'
  }
  return 'evasion'
}

export function DecisionRail() {
  const decisions = useEventStore((s) => s.decisions)
  const signals = useEventStore((s) => s.signals)
  const attributionsById = useEventStore((s) => s.attributionsById)
  const attributions = useEventStore((s) => s.attributions)
  const decisionReviews = useEventStore((s) => s.decisionReviews)
  const acceptedDecisionIds = useEventStore((s) => s.acceptedDecisionIds)
  const deferredDecisionIds = useEventStore((s) => s.deferredDecisionIds)
  const reviewDecision = useEventStore((s) => s.reviewDecision)
  const clearDecisionStatus = useEventStore((s) => s.clearDecisionStatus)
  const startManeuverDemo = useEventStore((s) => s.startManeuverDemo)
  const setActiveTraceRefId = useEventStore((s) => s.setActiveTraceRefId)
  const traces = useEventStore((s) => s.traces)

  const needApprovalCount = useMemo(() => {
    return decisions.filter((d) => {
      if (d.authority !== 'request') return false
      const rev = decisionReviews[d.id]
      if (rev && (rev.status === 'approved' || rev.status === 'rejected')) {
        return false
      }
      return !acceptedDecisionIds.has(d.id) && !deferredDecisionIds.has(d.id)
    }).length
  }, [decisions, decisionReviews, acceptedDecisionIds, deferredDecisionIds])

  const handleViewTrace = (decision: Decision) => {
    setActiveTraceRefId(decision.id)

    // Smoothly scroll Reasoning Trace panel to the matching trace line
    setTimeout(() => {
      const candidates = [
        document.querySelector(`[data-ref-id="${decision.id}"]`),
        decision.attribution_id
          ? document.querySelector(`[data-ref-id="${decision.attribution_id}"]`)
          : null,
        document.querySelector('.reasoning-panel__stream'),
      ]

      const targetEl = candidates.find(Boolean) as HTMLElement | null
      if (targetEl && targetEl !== candidates[2]) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }, 50)
  }

  const handleApprove = (d: Decision) => {
    reviewDecision(d.id, 'approved', 'OPERATOR-01')

    // Also trigger the orbital maneuver animation if applicable
    const packet = (d.request_packet ?? {}) as Record<string, unknown>
    const burn = (packet.recommended_burn ?? {}) as Record<string, unknown>
    if (burn.sat || packet.pre_miss_km) {
      const preMissKm = Number(packet.pre_miss_km ?? 0)
      const postMissKm = Number(packet.post_miss_km ?? preMissKm + 80)
      const dvMs = Number(burn.dv_m_s ?? 1.5)
      startManeuverDemo({
        decisionId: d.id,
        startedAt: Date.now(),
        durationMs: 15000,
        preMissKm,
        postMissKm,
        dvMs,
        friendlyLabel: typeof burn.sat === 'string' ? burn.sat : undefined,
        hostileLabel:
          typeof burn.against === 'string' ? burn.against : undefined,
        demoType: actionToDemoType(d.action),
      })
    }
  }

  const handleReject = (d: Decision) => {
    reviewDecision(d.id, 'rejected', 'OPERATOR-01')
  }

  return (
    <div className="panel operator-panel--decision">
      <div className="panel__header">
        <h2>Decision Rail</h2>
        <span>
          {decisions.length} {decisions.length === 1 ? 'decision' : 'decisions'}
          {needApprovalCount > 0 ? (
            <strong className="decision-rail__header-alert">
              {' '}· {needApprovalCount} needs approval
            </strong>
          ) : (
            ' · 0 pending'
          )}
        </span>
      </div>

      {decisions.length === 0 ? (
        <p className="operator-shell__empty">No authority requests</p>
      ) : (
        <div className="decision-rail__list">
          {decisions.map((d) => {
            const attribution =
              attributionsById[d.attribution_id] ??
              attributions.find((a) => a.id === d.attribution_id) ??
              null

            // Derive review state
            const review = decisionReviews[d.id] ?? null
            const isApproved =
              review?.status === 'approved' || acceptedDecisionIds.has(d.id)
            const isRejected =
              review?.status === 'rejected' || deferredDecisionIds.has(d.id)
            const isReviewed = isApproved || isRejected
            const statusLabel = isApproved ? 'APPROVED' : isRejected ? 'REJECTED' : null
            const reviewActor = review?.actor ?? (isReviewed ? 'OPERATOR-01' : null)
            const reviewTimestamp = review?.timestamp ?? (isReviewed ? d.ts : null)

            // Reconcile rationale from real backend data (first-class field, evidence line, or trace)
            const reconcileRationale =
              attribution?.reconcile_rationale ||
              attribution?.evidence.find(
                (e) =>
                  e.startsWith('Red-team review:') ||
                  e.startsWith('Reconciled:'),
              ) ||
              traces.find(
                (t: ReasoningTrace) =>
                  t.stage === 'attrib_reconcile' &&
                  (t.ref_id === d.attribution_id || t.ref_id === d.id),
              )?.message ||
              null

            // Red-team challenge from real backend data (first-class field, evidence objection, or trace)
            const redTeamChallenge =
              attribution?.redteam_challenge ||
              attribution?.evidence.find((e) =>
                e.startsWith('Red-team objection:'),
              ) ||
              traces.find(
                (t: ReasoningTrace) =>
                  t.stage === 'attrib_redteam' &&
                  (t.ref_id === d.attribution_id ||
                    (attribution &&
                      attribution.anomaly_ids?.includes(t.ref_id || ''))),
              )?.message ||
              null

            // Supporting signals and KB counts from real backend data
            const supportingSignalsCount =
              d.source_signal_ids?.length ||
              attribution?.source_signal_ids?.length ||
              0
            const kbEntriesCount = attribution?.kb_citations?.length || 0

            // Recipient from request packet or default authority routing
            const requestPacket = d.request_packet as Record<string, unknown> | null
            const recipient =
              (requestPacket?.to as string) ||
              (d.authority === 'request' ? 'CJFSCC' : 'Local Commander')

            const actor =
              attribution?.actor ||
              (requestPacket?.actor as string) ||
              'Unattributed'
            const confidence =
              attribution?.confidence ??
              (typeof requestPacket?.confidence === 'number'
                ? requestPacket.confidence
                : null)
            const confidencePct =
              confidence !== null ? Math.round(confidence * 100) : null

            const needsApproval = d.authority === 'request' && !isReviewed

            return (
              <article
                key={d.id}
                className={`decision-card${
                  isApproved
                    ? ' decision-card--approved'
                    : isRejected
                    ? ' decision-card--rejected'
                    : needsApproval
                    ? ' decision-card--needs-approval'
                    : ''
                }`}
              >
                {/* Header: Action + Authority + Target */}
                <div className="decision-card__top">
                  <div className="decision-card__action-group">
                    <span className="decision-card__action-name">
                      {formatAction(d.action)}
                    </span>
                    <span className="decision-card__action-tag">
                      {d.action}
                    </span>
                  </div>
                  <div className="decision-card__badges">
                    <span
                      className={`decision-card__badge decision-card__badge--${d.authority}`}
                    >
                      {d.authority.toUpperCase()}
                    </span>
                    {needsApproval ? (
                      <span className="decision-card__badge decision-card__badge--needs-approval">
                        NEEDS APPROVAL
                      </span>
                    ) : null}
                  </div>
                </div>

                {/* Target */}
                <div className="decision-card__target-row">
                  <span className="decision-card__target-label">Target:</span>
                  <span className="decision-card__target-val">{d.target}</span>
                </div>

                {/* Attributed Actor + Confidence Bar */}
                <div className="decision-card__actor-section">
                  <div className="decision-card__actor-head">
                    <span className="decision-card__actor-name">
                      <span className="decision-card__actor-tag">Actor:</span>{' '}
                      {actor}
                    </span>
                    {confidencePct !== null ? (
                      <span className="decision-card__conf-text">
                        {confidencePct}% conf
                      </span>
                    ) : null}
                  </div>
                  {confidencePct !== null ? (
                    <div
                      className="decision-card__conf-bar"
                      role="progressbar"
                      aria-valuenow={confidencePct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <div
                        className={`decision-card__conf-fill${
                          confidencePct >= 70
                            ? ' decision-card__conf-fill--high'
                            : confidencePct >= 50
                            ? ' decision-card__conf-fill--med'
                            : ' decision-card__conf-fill--low'
                        }`}
                        style={{ width: `${confidencePct}%` }}
                      />
                    </div>
                  ) : null}
                </div>

                {/* Rationale from reconcile step */}
                {reconcileRationale ? (
                  <div className="decision-card__field">
                    <span className="decision-card__field-label">
                      Reconcile Rationale:
                    </span>
                    <p className="decision-card__field-text">
                      {reconcileRationale}
                    </p>
                  </div>
                ) : null}

                {/* Red-team challenge line */}
                {redTeamChallenge ? (
                  <div className="decision-card__field decision-card__field--challenge">
                    <span className="decision-card__field-label">
                      Red-Team Challenge:
                    </span>
                    <p className="decision-card__field-text">
                      {redTeamChallenge}
                    </p>
                  </div>
                ) : null}

                {/* Metadata counts & Recipient */}
                <div className="decision-card__meta-chips">
                  {supportingSignalsCount > 0 ? (
                    <span className="decision-card__chip">
                      ⚡ {supportingSignalsCount}{' '}
                      {supportingSignalsCount === 1 ? 'signal' : 'signals'}
                    </span>
                  ) : null}
                  {kbEntriesCount > 0 ? (
                    <span className="decision-card__chip">
                      📖 {kbEntriesCount} KB{' '}
                      {kbEntriesCount === 1 ? 'entry' : 'entries'}
                    </span>
                  ) : null}
                  {recipient ? (
                    <span className="decision-card__chip decision-card__chip--recipient">
                      ✉ To: {recipient}
                    </span>
                  ) : null}
                </div>

                {/* Status banner when reviewed */}
                {isReviewed ? (
                  <div className="decision-card__status-banner">
                    <span
                      className={`decision-card__status-tag decision-card__status-tag--${
                        isApproved ? 'approved' : 'rejected'
                      }`}
                    >
                      {statusLabel}
                    </span>
                    <span className="decision-card__status-meta">
                      {reviewActor} · {formatTime(reviewTimestamp || d.ts)}
                    </span>
                    <button
                      type="button"
                      className="decision-card__btn-reconsider"
                      onClick={() => clearDecisionStatus(d.id)}
                      title="Undo review status"
                    >
                      Reconsider
                    </button>
                  </div>
                ) : null}

                {/* Action buttons (Approve / Reject / View trace) */}
                <div className="decision-card__actions" role="group">
                  {!isReviewed ? (
                    <>
                      <button
                        type="button"
                        className="decision-card__btn decision-card__btn--approve"
                        onClick={() => handleApprove(d)}
                      >
                        ✓ Approve
                      </button>
                      <button
                        type="button"
                        className="decision-card__btn decision-card__btn--reject"
                        onClick={() => handleReject(d)}
                      >
                        ✕ Reject
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    className="decision-card__btn decision-card__btn--trace"
                    onClick={() => handleViewTrace(d)}
                    title="Highlight related reasoning traces"
                  >
                    View trace
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}

      <div className="operator-shell__footer" aria-live="polite">
        {signals.length} signals streamed
      </div>
    </div>
  )
}
