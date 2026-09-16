import type { FieldMapping, FieldSample, TargetField, TransformOp, Vocabulary } from '../types/api'
import { TransformEditor } from './TransformEditor'

type Props = {
  field: TargetField
  isKey: boolean
  mapping: FieldMapping
  columns: string[]
  vocab: Vocabulary
  samples: FieldSample[]
  problems: number
  onColumn: (column: string | null) => void
  onTransforms: (transforms: TransformOp[]) => void
}

export function MappingRow({
  field,
  isKey,
  mapping,
  columns,
  vocab,
  samples,
  problems,
  onColumn,
  onTransforms,
}: Props) {
  const mapped = mapping.source_column !== null || mapping.literal !== null
  const nullTokens = new Set(
    mapping.transforms
      .filter((op) => op.op === 'null_if')
      .flatMap((op) => (Array.isArray(op.args.tokens) ? op.args.tokens : []))
      .map((token) => String(token).trim().toLowerCase()),
  )
  const lost = (sample: FieldSample) =>
    sample.value === null &&
    sample.raw !== null &&
    !nullTokens.has(sample.raw.trim().toLowerCase())
  const classes = ['mapping-row']
  if (problems > 0) classes.push('has-problems')
  if (!mapped && !field.nullable) classes.push('needs-column')

  return (
    <article className={classes.join(' ')}>
      <div>
        <div className="mapping-name">
          <code>{field.name}</code>
          <span className="type">{field.dtype}</span>
          {isKey && (
            <span className="tag" title="Part of the primary key">
              key
            </span>
          )}
        </div>
        <div className="muted small">{field.nullable ? 'optional' : 'required'}</div>
      </div>

      <div className="mapping-source">
        <select
          aria-label={`Source column for ${field.name}`}
          value={mapping.source_column ?? ''}
          onChange={(e) => onColumn(e.target.value || null)}
        >
          <option value="">— not mapped —</option>
          {columns.map((column) => (
            <option key={column} value={column}>
              {column}
            </option>
          ))}
        </select>
        <Confidence mapping={mapping} mapped={mapped} />
      </div>

      <div>
        {mapped ? (
          <TransformEditor
            ops={mapping.transforms}
            dtype={field.dtype}
            enumValues={field.constraints.enum}
            vocab={vocab}
            onChange={onTransforms}
          />
        ) : (
          <span className={field.nullable ? 'muted small' : 'error small'}>
            {field.nullable ? 'Left empty.' : 'Required — without a column every row is rejected.'}
          </span>
        )}
      </div>

      <div>
        {problems > 0 && (
          <div className="problem-count">
            {problems} {problems === 1 ? 'row fails' : 'rows fail'}
          </div>
        )}
        {mapped && samples.length > 0 && (
          <ul className="sample-pairs" aria-label={`Sample values for ${field.name}`}>
            {samples.map((sample) => (
              <li key={sample.line} title={`Line ${sample.line}`}>
                <code className="raw">{sample.raw ?? '∅'}</code>
                <span className="arrow">→</span>
                <code className={lost(sample) ? 'lost' : undefined}>
                  {sample.value ?? 'null'}
                </code>
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  )
}

function Confidence({ mapping, mapped }: { mapping: FieldMapping; mapped: boolean }) {
  if (mapping.decided_by === 'human') return <span className="pill human">edited</span>
  if (!mapped) return null

  const level = mapping.confidence >= 0.75 ? 'high' : mapping.confidence >= 0.5 ? 'mid' : 'low'
  return (
    <details className="evidence">
      <summary>
        <span className={`pill ${level}`}>{Math.round(mapping.confidence * 100)}% confident</span>
      </summary>
      <ul>
        {mapping.provenance.map((evidence, index) => (
          <li key={index}>
            <strong>{evidence.provider}</strong> {Math.round(evidence.score * 100)}%{' '}
            <span className="muted">{evidence.detail}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
