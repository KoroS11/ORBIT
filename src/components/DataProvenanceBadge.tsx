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
}

export function DataProvenanceBadge() {
  const [provider, setProvider] = useState<string>('stub')

  useEffect(() => {
    const apiUrl = getApiBaseUrl()

    const fetchHealth = () => {
      fetch(`${apiUrl}/health`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data: HealthResponse | null) => {
          if (data?.active_provider) {
            setProvider(data.active_provider)
          } else if (data?.provider) {
            setProvider(data.provider)
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

  return (
    <div
      className="data-provenance-badge"
      title={TOOLTIP_TEXT}
      aria-label={TOOLTIP_TEXT}
    >
      <span className="data-provenance-badge__dot" />
      <span className="data-provenance-badge__provider">{provider}</span>
      <span className="data-provenance-badge__divider">|</span>
      <span className="data-provenance-badge__synthetic">
        SYNTHETIC SCENARIO DATA
      </span>
    </div>
  )
}
