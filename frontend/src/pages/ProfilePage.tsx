import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { ColumnCard } from '../components/ColumnCard'
import type { ColumnProfile, Preview, SampleFile, SourceSummary } from '../types/api'

type Load = { state: 'idle' } | { state: 'busy' } | { state: 'error'; message: string }

export function ProfilePage() {
  const [samples, setSamples] = useState<SampleFile[]>([])
  const [source, setSource] = useState<SourceSummary | null>(null)
  const [profiles, setProfiles] = useState<ColumnProfile[]>([])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [load, setLoad] = useState<Load>({ state: 'idle' })
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.samples().then(setSamples).catch(() => setSamples([]))
  }, [])

  async function show(summary: SourceSummary) {
    setSource(summary)
    const [p, pv] = await Promise.all([
      api.profile(summary.source_id),
      api.preview(summary.source_id, 12),
    ])
    setProfiles(p)
    setPreview(pv)
  }

  async function run(work: () => Promise<SourceSummary>) {
    setLoad({ state: 'busy' })
    try {
      await show(await work())
      setLoad({ state: 'idle' })
    } catch (error) {
      setLoad({ state: 'error', message: (error as Error).message })
    }
  }

  const busy = load.state === 'busy'

  return (
    <>
      <section className="panel">
        <h2>Source file</h2>
        <div className="sample-row">
          {samples.map((sample) => (
            <button
              key={sample.name}
              disabled={busy}
              onClick={() => run(() => api.ingestSample(sample.name))}
            >
              {sample.name}
              <span className="muted"> {(sample.bytes / 1024).toFixed(1)}kB</span>
            </button>
          ))}
          <button disabled={busy} onClick={() => fileInput.current?.click()}>
            Upload your own…
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.json,.jsonl,.ndjson"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) run(() => api.upload(file))
              event.target.value = ''
            }}
          />
        </div>
        {busy && <p className="muted">Landing and profiling…</p>}
        {load.state === 'error' && <p className="error">{load.message}</p>}
      </section>

      {source && (
        <section className="panel">
          <h2>Landed</h2>
          <dl className="stats inline">
            <dt>File</dt>
            <dd>
              {source.filename}
              {source.sheet && <span className="muted"> · sheet {source.sheet}</span>}
            </dd>
            <dt>Shape</dt>
            <dd>
              {source.row_count} rows × {source.column_count} columns
            </dd>
            {Object.entries(source.sniff)
              .filter(([key]) => key !== 'preamble' && key !== 'columns' && key !== 'sheets')
              .map(([key, value]) => (
                <div key={key} className="contents">
                  <dt>{key.replace(/_/g, ' ')}</dt>
                  <dd>
                    <code>{JSON.stringify(value)}</code>
                  </dd>
                </div>
              ))}
          </dl>
          {Array.isArray(source.sniff.preamble) && source.sniff.preamble.length > 0 && (
            <details className="preamble">
              <summary>{source.sniff.preamble.length} preamble rows skipped</summary>
              <pre>{(source.sniff.preamble as string[]).join('\n')}</pre>
            </details>
          )}
        </section>
      )}

      {profiles.length > 0 && (
        <section className="panel">
          <h2>Column profiles</h2>
          <div className="column-grid">
            {profiles.map((profile) => (
              <ColumnCard key={profile.name} profile={profile} />
            ))}
          </div>
        </section>
      )}

      {preview && (
        <section className="panel">
          <h2>Landed rows</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {preview.columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, index) => (
                  <tr key={index}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>
                        {cell === null ? <span className="muted">null</span> : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  )
}
