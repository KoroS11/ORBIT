import { useEffect, useState } from 'react'
import { getApiBaseUrl } from '../lib/apiConfig'

const TOOLTIP_TEXT =
  'Scenario signals are authored (synthetic); orbital propagation uses cached real orbital elements where available.'

interface HealthResponse {
  status: string
  provider?: string
  stub_fallback_active?: boolean
  fallback_count?: number
  last_fallback_at?: string | null
  active_provider?: string
  cache_hits?: number
  seed_hits?: number
  live_calls?: number
  stub_fallbacks?: number
}

export function DataProvenanceBadge() {
  const [provider, setProvider] = useState<string>('stub')
  const [telemetry, setTelemetry] = useState<HealthResponse | null>(null)

  useEffect(() => {
    const apiUrl = getApiBaseUrl()

    const fetchHealth = () => {
      fetch(`${apiUrl}/health`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data: HealthResponse | null) => {
          if (data) {
            setTelemetry(data)
            if (data.active_provider) {
              setProvider(data.active_provider)
            } else if (data.provider) {
              setProvider(data.provider)
            }
          }
        })
        .catch(() => {
          // Default if offline
          setProvider('stub')
        })
    }

    fetchHealth()
    const intervalId = setInterval(fetchHealth, 30000)
    return () => clearInterval(intervalId)
  }, [])

  const hasCounters =
    telemetry !== null &&
    (telemetry.cache_hits !== undefined ||
      telemetry.seed_hits !== undefined ||
      telemetry.live_calls !== undefined ||
      telemetry.stub_fallbacks !== undefined)

  const breakdownText = hasCounters
    ? `cache:${telemetry?.cache_hits ?? 0} seed:${telemetry?.seed_hits ?? 0} live:${telemetry?.live_calls ?? 0} stub:${telemetry?.stub_fallbacks ?? 0}`
    : ''

  const fullTooltip = breakdownText
    ? `${TOOLTIP_TEXT} (LLM Calls: ${breakdownText})`
    : TOOLTIP_TEXT

  return (
    <div
      className="data-provenance-badge"
      title={fullTooltip}
      aria-label={fullTooltip}
    >
      <span className="data-provenance-badge__dot" />
      <span className="data-provenance-badge__provider">{provider}</span>
      {hasCounters && (
        <>
          <span className="data-provenance-badge__divider">|</span>
          <span
            className="data-provenance-badge__breakdown"
            style={{ color: '#94a3b8', fontSize: '0.74rem' }}
          >
            {breakdownText}
          </span>
        </>
      )}
      <span className="data-provenance-badge__divider">|</span>
      <span className="data-provenance-badge__synthetic">
        SYNTHETIC SCENARIO DATA
      </span>
    </div>
  )
}
