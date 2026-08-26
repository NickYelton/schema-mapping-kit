import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { HealthResponse } from './types/api'
import './App.css'

type Status = { state: 'loading' } | { state: 'ok'; data: HealthResponse } | { state: 'error'; message: string }

export default function App() {
  const [status, setStatus] = useState<Status>({ state: 'loading' })

  useEffect(() => {
    api
      .health()
      .then((data) => setStatus({ state: 'ok', data }))
      .catch((error: Error) => setStatus({ state: 'error', message: error.message }))
  }, [])

  return (
    <main className="shell">
      <header>
        <h1>Schema Mapping &amp; Onboarding Kit</h1>
        <p className="tagline">
          Profile a messy file, review the proposed mapping, compile a deterministic transform.
        </p>
      </header>

      <section className="panel">
        <h2>Backend</h2>
        {status.state === 'loading' && <p className="muted">Checking…</p>}
        {status.state === 'error' && (
          <p className="error">
            Cannot reach the API. Is the backend running on :8000? <span>({status.message})</span>
          </p>
        )}
        {status.state === 'ok' && (
          <dl>
            <dt>Status</dt>
            <dd>{status.data.status}</dd>
            <dt>LLM provider</dt>
            <dd>{status.data.llm_provider}</dd>
            <dt>Catalog tables</dt>
            <dd>{status.data.tables.join(', ')}</dd>
          </dl>
        )}
      </section>
    </main>
  )
}
