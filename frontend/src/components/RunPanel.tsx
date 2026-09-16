import { useState } from 'react'
import { api } from '../api/client'
import type { RunResponse } from '../types/api'

type Engine = 'polars' | 'duckdb'
type Kind = 'sql' | 'python'

type Props = {
  sourceId: string
  version: number
  blocked: string | null
}

export function RunPanel({ sourceId, version, blocked }: Props) {
  const [engine, setEngine] = useState<Engine>('polars')
  const [run, setRun] = useState<(RunResponse & { version: number }) | null>(null)
  const [compiled, setCompiled] = useState<{ kind: Kind; version: number; source: string } | null>(
    null,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const showing = compiled?.version === version ? compiled : null

  async function execute() {
    setBusy(true)
    setError(null)
    try {
      setRun({ ...(await api.run(sourceId, engine)), version })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function toggle(kind: Kind) {
    if (showing?.kind === kind) {
      setCompiled(null)
      return
    }
    setError(null)
    try {
      const result = await api.compiled(sourceId, kind)
      setCompiled({ kind, version, source: result.source })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <section className="panel">
      <h2>Run the transform</h2>
      <p className="muted">
        A run compiles saved version v{version} to SQL and Python, applies it to every row, and
        validates the result. The LLM plays no part.
      </p>
      <div className="actions">
        <label className="arg">
          engine
          <select value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
            <option value="polars">Polars</option>
            <option value="duckdb">DuckDB</option>
          </select>
        </label>
        <button className="primary" onClick={execute} disabled={busy || blocked !== null}>
          {busy ? 'Running…' : 'Run'}
        </button>
        <button onClick={() => toggle('sql')} disabled={blocked !== null} aria-pressed={showing?.kind === 'sql'}>
          View SQL
        </button>
        <button
          onClick={() => toggle('python')}
          disabled={blocked !== null}
          aria-pressed={showing?.kind === 'python'}
        >
          View Python
        </button>
      </div>
      {blocked && <p className="warn">{blocked}</p>}
      {error && <p className="error">{error}</p>}
      {showing && <pre className="compiled">{showing.source}</pre>}

      {run && (
        <div className="run-result">
          <p>
            <strong>{run.summary}</strong>{' '}
            <span className="muted">
              · v{run.version} on {run.engine === 'duckdb' ? 'DuckDB' : 'Polars'}
            </span>
          </p>
          {run.rows_rejected ? (
            <>
              <ul className="problems compact">
                {run.groups.map((group) => (
                  <li key={group.message} className="problem">
                    <span className="problem-count">{group.count}×</span>
                    <div className="problem-body">
                      <p>{group.message}</p>
                    </div>
                  </li>
                ))}
              </ul>
              <a className="button" href={api.rejectionsUrl(sourceId, run.run_id)} download>
                Download rejections CSV
              </a>
            </>
          ) : null}
          <p className="muted small">
            Valid rows written to <code>{run.output_path}</code>
          </p>
        </div>
      )}
    </section>
  )
}
