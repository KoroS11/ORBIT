/**
 * Shared API & WebSocket configuration for ORBIT frontend.
 *
 * In production (served via Caddy reverse proxy), endpoints use same-origin
 * relative paths for REST (`/health`, `/scenarios`, etc.) and `wss://` (or `ws://`)
 * on `window.location.host` for the WebSocket stream.
 *
 * In local dev (port 5173), if VITE_ORBIT_API_URL is unset, it defaults to
 * http://127.0.0.1:8000 and ws://127.0.0.1:8000/ws.
 */

export function getApiBaseUrl(): string {
  if (import.meta.env.VITE_ORBIT_API_URL) {
    return import.meta.env.VITE_ORBIT_API_URL.replace(/\/$/, '')
  }
  if (typeof window !== 'undefined') {
    // If running on Vite dev (5173) or preview (4173) without reverse proxy, default to local backend
    if (window.location.port === '5173' || window.location.port === '4173') {
      return 'http://127.0.0.1:8000'
    }
    // In production same-origin deployment, use empty string for relative REST paths
    return ''
  }
  return 'http://127.0.0.1:8000'
}

export function getWsUrl(): string {
  if (import.meta.env.VITE_ORBIT_WS_URL) {
    return import.meta.env.VITE_ORBIT_WS_URL.trim()
  }
  if (import.meta.env.VITE_ORBIT_API_URL) {
    return import.meta.env.VITE_ORBIT_API_URL.replace(/^http/, 'ws').replace(/\/$/, '') + '/ws'
  }
  if (typeof window !== 'undefined') {
    if (window.location.port === '5173' || window.location.port === '4173') {
      return 'ws://127.0.0.1:8000/ws'
    }
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${window.location.host}/ws`
  }
  return 'ws://127.0.0.1:8000/ws'
}
