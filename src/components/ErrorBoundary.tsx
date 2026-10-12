import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  viewName?: string
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  public override state: State = {
    hasError: false,
    error: null,
  }

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  public override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error(`ErrorBoundary caught error in [${this.props.viewName ?? 'View'}]:`, error, errorInfo)
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  public override render() {
    if (this.state.hasError) {
      return (
        <section
          className="error-boundary-panel"
          style={{
            margin: '2rem auto',
            maxWidth: '640px',
            padding: '1.5rem',
            backgroundColor: 'rgba(26, 16, 24, 0.95)',
            border: '1px solid #ff4444',
            borderRadius: '6px',
            color: '#f0f0f0',
            fontFamily: 'var(--font-mono, monospace)',
          }}
          aria-live="assertive"
        >
          <header style={{ marginBottom: '1rem', borderBottom: '1px solid #44222a', paddingBottom: '0.5rem' }}>
            <span style={{ color: '#ff6666', fontWeight: 700, fontSize: '0.9rem', textTransform: 'uppercase' }}>
              Fault in {this.props.viewName ?? 'Component'}
            </span>
            <h2 style={{ fontSize: '1.2rem', margin: '0.25rem 0 0 0', color: '#ffffff' }}>
              Interface Error
            </h2>
          </header>
          <p style={{ margin: '0.5rem 0', fontSize: '0.9rem', color: '#ffaaaa' }}>
            {this.state.error?.message ?? 'An unexpected render error occurred.'}
          </p>
          <div style={{ marginTop: '1.25rem', display: 'flex', gap: '0.75rem' }}>
            <button
              type="button"
              onClick={this.handleReset}
              style={{
                background: '#ff4444',
                color: '#ffffff',
                border: 'none',
                padding: '0.5rem 1rem',
                borderRadius: '4px',
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.85rem',
              }}
            >
              Retry View
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                background: 'transparent',
                color: '#aaaaaa',
                border: '1px solid #555555',
                padding: '0.5rem 1rem',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '0.85rem',
              }}
            >
              Reload Page
            </button>
          </div>
        </section>
      )
    }

    return this.props.children
  }
}
