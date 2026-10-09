import { useEffect, useState } from 'react'

const TOOLTIP_TEXT =
  'Scenario signals are authored (synthetic); orbital propagation uses cached real orbital elements where available.'

interface HealthResponse {
  status: string
  provider?: string
  stub_fallback_active?: boolean
}

export function DataProvenanceBadge() {
  const [provider, setProvider] = useState<string>('stub')

  useEffect(() => {
    const apiUrl = import.meta.env.VITE_ORBIT_API_URL ?? 'http://127.0.0.1:8000'
    fetch(`${apiUrl}/health`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: HealthResponse | null) => {
        if (data?.provider) {
          setProvider(data.provider)
        }
      })
      .catch(() => {
        // Default if offline
        setProvider('stub')
      })
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
