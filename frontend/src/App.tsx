import { useEffect, useState } from 'react'
import { api } from './api/client'
import { TargetSchemaPanel } from './components/TargetSchemaPanel'
import { ProfilePage } from './pages/ProfilePage'
import { ReviewPage } from './pages/ReviewPage'
import type { HealthResponse, SourceSummary } from './types/api'
import './App.css'

type Tab = 'profile' | 'review'

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [offline, setOffline] = useState(false)
  const [source, setSource] = useState<SourceSummary | null>(null)
  const [tab, setTab] = useState<Tab>('profile')

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
          {source && (
            <nav className="tabs" aria-label="Workflow">
              <button
                className={tab === 'profile' ? 'tab active' : 'tab'}
                aria-pressed={tab === 'profile'}
                onClick={() => setTab('profile')}
              >
                1 · Profile
              </button>
              <button
                className={tab === 'review' ? 'tab active' : 'tab'}
                aria-pressed={tab === 'review'}
                onClick={() => setTab('review')}
              >
                2 · Review &amp; run
              </button>
              <span className="muted tab-source">{source.filename}</span>
            </nav>
          )}
          <div hidden={tab !== 'profile'}>
            <ProfilePage onSource={setSource} />
            <TargetSchemaPanel />
          </div>
          <div hidden={tab !== 'review'}>
            {source && <ReviewPage key={source.source_id} source={source} />}
          </div>
        </>
      )}
    </main>
  )
}
