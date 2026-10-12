import { Brigade } from './pages/Brigade'
import { Operator } from './pages/Operator'
import { ErrorBoundary } from './components/ErrorBoundary'
import './App.css'

function App() {
  const path = window.location.pathname

  return path.startsWith('/operator') ? (
    <ErrorBoundary viewName="Ops Console">
      <Operator />
    </ErrorBoundary>
  ) : (
    <ErrorBoundary viewName="Brigade COP">
      <Brigade />
    </ErrorBoundary>
  )
}

export default App
