import { useEffect, useState } from 'react'
import { api } from './api/client'
import { TargetSchemaPanel } from './components/TargetSchemaPanel'
import { ProfilePage } from './pages/ProfilePage'
import type { HealthResponse } from './types/api'
import './App.css'

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setOffline(true))
  }, [])

  return (
    <main className="shell">
      <header>
        <h1>Schema Mapping &amp; Onboarding Kit</h1>
        <p className="tagline">
          Profile a messy file, review the proposed mapping, compile a deterministic transform.
        </p>
        {health && (
          <p className="badge-row">
            <span className="badge">provider: {health.llm_provider}</span>
            <span className="badge">
              phase {health.phase}/{health.phase_total} · {health.phase_label}
            </span>
          </p>
        )}
      </header>

      {offline ? (
        <section className="panel">
          <p className="error">Cannot reach the API. Is the backend running on :8000?</p>
        </section>
      ) : (
        <>
          <ProfilePage />
          <TargetSchemaPanel />
        </>
      )}
    </main>
  )
}
