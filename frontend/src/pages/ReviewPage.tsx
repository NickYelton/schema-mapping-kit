import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { HistoryPanel } from '../components/HistoryPanel'
import { MappingRow } from '../components/MappingRow'
import { ProblemsPanel } from '../components/ProblemsPanel'
import { RunPanel } from '../components/RunPanel'
import { decisionKey, withValueMapping } from '../lib/transforms'
import type {
  FieldMapping,
  MappingSpec,
  PreviewResponse,
  ProviderStatus,
  SourceSummary,
  StoredSpec,
  TargetSchema,
  Vocabulary,
} from '../types/api'

const PREVIEW_DELAY_MS = 350

type Busy = 'loading' | 'proposing' | 'saving' | null

export function ReviewPage({ source }: { source: SourceSummary }) {
  const sourceId = source.source_id
  const [schema, setSchema] = useState<TargetSchema | null>(null)
  const [vocab, setVocab] = useState<Vocabulary | null>(null)
  const [stored, setStored] = useState<StoredSpec | null>(null)
  const [draft, setDraft] = useState<MappingSpec | null>(null)
  const [history, setHistory] = useState<StoredSpec[]>([])
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [busy, setBusy] = useState<Busy>('loading')
  const [error, setError] = useState<string | null>(null)
  const [useLlm, setUseLlm] = useState(true)
  const previewSeq = useRef(0)

  const adopt = useCallback((saved: StoredSpec) => {
    setStored(saved)
    setDraft(saved.spec)
  }, [])

  const refreshHistory = useCallback(
    () => api.history(sourceId).then(setHistory, () => setHistory([])),
    [sourceId],
  )

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [vocabulary, versions] = await Promise.all([api.vocabulary(), api.history(sourceId)])
        const current = versions[0] ?? null
        const target = await api.schema(current?.target_schema ?? 'orders', current?.target_version)
        if (cancelled) return
        setVocab(vocabulary)
        setSchema(target)
        setHistory(versions)
        if (current) adopt(current)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      } finally {
        if (!cancelled) setBusy(null)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [sourceId, adopt])

  useEffect(() => {
    if (!draft) return
    const seq = ++previewSeq.current
    const timer = window.setTimeout(() => {
      api
        .previewSpec(sourceId, draft)
        .then((result) => {
          if (seq !== previewSeq.current) return
          setPreview(result)
          setPreviewError(null)
        })
        .catch((e: Error) => {
          if (seq === previewSeq.current) setPreviewError(e.message)
        })
    }, PREVIEW_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [sourceId, draft])

  const updateMapping = useCallback((field: string, change: Partial<FieldMapping>) => {
    setDraft(
      (current) =>
        current && {
          ...current,
          mappings: current.mappings.map((m) =>
            m.target_field === field ? { ...m, ...change, decided_by: 'human' } : m,
          ),
        },
    )
  }, [])

  const mapValue = useCallback((field: string, raw: string, to: string) => {
    setDraft(
      (current) =>
        current && {
          ...current,
          mappings: current.mappings.map((m) =>
            m.target_field === field
              ? { ...m, transforms: withValueMapping(m.transforms, raw, to), decided_by: 'human' }
              : m,
          ),
        },
    )
  }, [])

  const dirty = stored !== null && draft !== null && decisionKey(draft) !== decisionKey(stored.spec)
  const used = new Set(draft?.mappings.map((m) => m.source_column) ?? [])
  const unmappedColumns = source.columns.filter((column) => !used.has(column))

  async function propose() {
    const replacesReview = dirty || stored?.created_by === 'human'
    if (
      replacesReview &&
      !window.confirm(
        'Re-proposing makes a fresh machine proposal the current version. Earlier versions stay in the history. Continue?',
      )
    ) {
      return
    }
    setBusy('proposing')
    setError(null)
    try {
      const result = await api.propose(sourceId, useLlm)
      setProviders(result.providers)
      adopt(await api.spec(sourceId))
      await refreshHistory()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function save() {
    if (!draft) return
    setBusy('saving')
    setError(null)
    try {
      adopt(await api.saveSpec(sourceId, { ...draft, unmapped_columns: unmappedColumns }))
      await refreshHistory()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function chooseColumn(field: string, column: string | null) {
    if (!schema) return
    if (column === null) {
      updateMapping(field, { source_column: null, literal: null, transforms: [] })
      return
    }
    try {
      const suggestion = await api.suggest(sourceId, column, field, schema.name, schema.version)
      updateMapping(field, { source_column: column, literal: null, transforms: suggestion.transforms })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (busy === 'loading') {
    return (
      <section className="panel">
        <p className="muted">Loading the mapping…</p>
      </section>
    )
  }

  if (!schema || !vocab) {
    return (
      <section className="panel">
        <p className="error">{error ?? 'Could not load the target schema.'}</p>
      </section>
    )
  }

  if (!draft || !stored) {
    return (
      <section className="panel">
        <h2>Propose a mapping</h2>
        <p>
          Nothing has been mapped for <strong>{source.filename}</strong> yet. The proposer matches
          its {source.column_count} columns to{' '}
          <code>
            {schema.name} v{schema.version}
          </code>{' '}
          and suggests the cleanup each one needs; you confirm or correct the result here.
        </p>
        <label className="check">
          <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
          Ask the LLM as well as the heuristics and embeddings
        </label>
        <div className="actions">
          <button className="primary" disabled={busy !== null} onClick={propose}>
            {busy === 'proposing' ? 'Proposing…' : 'Propose mapping'}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>
    )
  }

  const byField = new Map(draft.mappings.map((m) => [m.target_field, m]))

  return (
    <>
      <section className="panel">
        <div className="review-head">
          <div>
            <h2>Review mapping</h2>
            <p className="review-title">
              <strong>{source.filename}</strong> →{' '}
              <code>
                {schema.name} v{schema.version}
              </code>{' '}
              <span className="muted">
                · saved as v{stored.version} by{' '}
                {stored.created_by === 'human' ? 'a reviewer' : 'the proposer'}
              </span>
            </p>
          </div>
          <div className="actions">
            <button onClick={propose} disabled={busy !== null}>
              {busy === 'proposing' ? 'Proposing…' : 'Re-propose'}
            </button>
            <button onClick={() => setDraft(stored.spec)} disabled={!dirty || busy !== null}>
              Discard changes
            </button>
            <button
              className="primary"
              onClick={save}
              disabled={!dirty || previewError !== null || busy !== null}
            >
              {busy === 'saving' ? 'Saving…' : `Save as v${stored.version + 1}`}
            </button>
          </div>
        </div>
        <PassMeter preview={preview} error={previewError} dirty={dirty} />
        {providers.length > 0 && (
          <p className="providers">
            {providers.map((p) => (
              <span key={p.provider} className={p.available ? 'badge' : 'badge off'} title={p.detail}>
                {p.provider}
              </span>
            ))}
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      <section className="panel">
        <h2>Fields</h2>
        <div className="mapping-list">
          {schema.fields.map((field) => {
            const mapping = byField.get(field.name)
            if (!mapping) return null
            return (
              <MappingRow
                key={field.name}
                field={field}
                isKey={schema.primary_key.includes(field.name)}
                mapping={mapping}
                columns={source.columns}
                vocab={vocab}
                samples={preview?.samples[field.name] ?? []}
                problems={preview?.field_problems[field.name] ?? 0}
                onColumn={(column) => void chooseColumn(field.name, column)}
                onTransforms={(transforms) => updateMapping(field.name, { transforms })}
              />
            )
          })}
        </div>
      </section>

      <ProblemsPanel groups={preview?.groups ?? []} schema={schema} onMapValue={mapValue} />

      {unmappedColumns.length > 0 && (
        <section className="panel">
          <h2>Columns not used</h2>
          <p className="muted">
            These columns don’t feed any field. That’s fine when they hold nothing the target needs.
          </p>
          <div className="sample-row">
            {unmappedColumns.map((column) => (
              <code key={column} className="chip">
                {column}
              </code>
            ))}
          </div>
        </section>
      )}

      <RunPanel
        sourceId={sourceId}
        version={stored.version}
        blocked={dirty ? 'Save your changes to run them — a run always uses the saved version.' : null}
      />

      <HistoryPanel history={history} />
    </>
  )
}

function PassMeter({
  preview,
  error,
  dirty,
}: {
  preview: PreviewResponse | null
  error: string | null
  dirty: boolean
}) {
  if (error) return <p className="error">This draft can’t be compiled: {error}</p>
  if (!preview) return <p className="muted">Checking the draft against every row…</p>

  const rate = preview.rows_in ? preview.rows_valid / preview.rows_in : 0
  return (
    <div className="pass">
      <div
        className="pass-bar"
        role="meter"
        aria-label="Rows passing validation"
        aria-valuemin={0}
        aria-valuemax={preview.rows_in}
        aria-valuenow={preview.rows_valid}
      >
        <div className={rate === 1 ? 'pass-fill ok' : 'pass-fill'} style={{ width: `${rate * 100}%` }} />
      </div>
      <span>
        {dirty && <span className="muted">With your changes: </span>}
        {preview.summary}
      </span>
    </div>
  )
}
