import {
  DEFAULT_ARGS,
  describeOp,
  insertBeforeCast,
  parseTokens,
  tokensText,
} from '../lib/transforms'
import type { TransformOp, Vocabulary } from '../types/api'

type Props = {
  ops: TransformOp[]
  dtype: string
  enumValues: string[] | null
  vocab: Vocabulary
  onChange: (ops: TransformOp[]) => void
}

export function TransformEditor({ ops, dtype, enumValues, vocab, onChange }: Props) {
  const hasCast = ops.length > 0 && ops[ops.length - 1].op === 'cast'
  const steps = hasCast ? ops.slice(0, -1) : ops
  const tail = hasCast ? ops.slice(-1) : []

  const commit = (next: TransformOp[]) => onChange([...next, ...tail])
  const replace = (index: number, op: TransformOp) =>
    commit(steps.map((step, i) => (i === index ? op : step)))
  const remove = (index: number) => commit(steps.filter((_, i) => i !== index))
  const move = (index: number, delta: number) => {
    const next = [...steps]
    const [op] = next.splice(index, 1)
    next.splice(index + delta, 0, op)
    commit(next)
  }

  return (
    <div className="steps">
      <ol>
        {steps.map((op, index) => (
          <li key={index} className="step">
            <div className="step-head">
              <span className="step-name" title={describeOp(op)}>
                {op.op}
              </span>
              <span className="step-tools">
                <button
                  className="icon"
                  aria-label={`Move ${op.op} up`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  className="icon"
                  aria-label={`Move ${op.op} down`}
                  disabled={index === steps.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button className="icon" aria-label={`Remove ${op.op}`} onClick={() => remove(index)}>
                  ×
                </button>
              </span>
            </div>
            <StepArgs
              op={op}
              enumValues={enumValues}
              vocab={vocab}
              onChange={(next) => replace(index, next)}
            />
          </li>
        ))}
        {tail.map((op) => (
          <li key="cast" className="step fixed" title="Every pipeline ends by casting to the field type">
            <span className="step-name">cast</span>{' '}
            <span className="muted">→ {String(op.args.dtype ?? dtype)}</span>
          </li>
        ))}
      </ol>
      <select
        aria-label="Add a step"
        value=""
        onChange={(e) => {
          const name = e.target.value
          if (!name) return
          onChange(insertBeforeCast(ops, { op: name, args: structuredClone(DEFAULT_ARGS[name] ?? {}) }))
        }}
      >
        <option value="">+ add step</option>
        {vocab.ops
          .filter((spec) => spec.op !== 'cast')
          .map((spec) => (
            <option key={spec.op} value={spec.op}>
              {describeOp({ op: spec.op, args: {} })}
            </option>
          ))}
      </select>
    </div>
  )
}

type StepProps = {
  op: TransformOp
  enumValues: string[] | null
  vocab: Vocabulary
  onChange: (op: TransformOp) => void
}

function StepArgs({ op, enumValues, vocab, onChange }: StepProps) {
  const set = (name: string, value: unknown) => onChange({ ...op, args: { ...op.args, [name]: value } })

  switch (op.op) {
    case 'null_if':
      return <TokensInput tokens={op.args.tokens} onChange={(tokens) => set('tokens', tokens)} />
    case 'parse_decimal':
      return (
        <label className="arg">
          written as
          <select value={String(op.args.locale ?? 'us')} onChange={(e) => set('locale', e.target.value)}>
            {vocab.decimal_locales.map((locale) => (
              <option key={locale} value={locale}>
                {locale === 'eu' ? '1.234,56' : '1,234.56'}
              </option>
            ))}
          </select>
        </label>
      )
    case 'parse_date': {
      const format = String(op.args.format ?? '')
      const known = vocab.date_formats.some((d) => d.format === format)
      return (
        <label className="arg">
          format
          <select value={format} onChange={(e) => set('format', e.target.value)}>
            {!known && <option value={format}>{format}</option>}
            {vocab.date_formats.map((d) => (
              <option key={d.format} value={d.format}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
      )
    }
    case 'map_values':
      return (
        <ValueMap
          mapping={(op.args.mapping ?? {}) as Record<string, string>}
          enumValues={enumValues}
          onChange={(mapping) => set('mapping', mapping)}
        />
      )
    case 'regex_extract':
      return (
        <div className="arg-row">
          <label className="arg">
            pattern
            <input value={String(op.args.pattern ?? '')} onChange={(e) => set('pattern', e.target.value)} />
          </label>
          <label className="arg">
            group
            <input
              type="number"
              min={0}
              value={Number(op.args.group ?? 1)}
              onChange={(e) => set('group', Number(e.target.value))}
            />
          </label>
        </div>
      )
    case 'default':
      return (
        <label className="arg">
          fill empty with
          <input value={String(op.args.value ?? '')} onChange={(e) => set('value', e.target.value)} />
        </label>
      )
    default:
      return <span className="muted small">{describeOp(op)}</span>
  }
}

function TokensInput({ tokens, onChange }: { tokens: unknown; onChange: (tokens: string[]) => void }) {
  const text = tokensText(tokens)
  return (
    <label className="arg">
      treat as empty
      <input
        key={text}
        defaultValue={text}
        placeholder="∅, -, N/A"
        title="Comma-separated. ∅ stands for a blank cell."
        onBlur={(e) => onChange(parseTokens(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
        }}
      />
    </label>
  )
}

type ValueMapProps = {
  mapping: Record<string, string>
  enumValues: string[] | null
  onChange: (mapping: Record<string, string>) => void
}

function ValueMap({ mapping, enumValues, onChange }: ValueMapProps) {
  const entries = Object.entries(mapping)
  const update = (index: number, from: string, to: string) =>
    onChange(Object.fromEntries(entries.map((entry, i) => (i === index ? [from, to] : entry))))
  const remove = (index: number) => onChange(Object.fromEntries(entries.filter((_, i) => i !== index)))

  return (
    <div className="value-map">
      {entries.map(([from, to], index) => (
        <div key={index} className="value-map-row">
          <input aria-label="Value in the file" value={from} onChange={(e) => update(index, e.target.value, to)} />
          <span className="arrow">→</span>
          {enumValues ? (
            <select aria-label="Canonical value" value={to} onChange={(e) => update(index, from, e.target.value)}>
              {!enumValues.includes(to) && <option value={to}>{to}</option>}
              {enumValues.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          ) : (
            <input aria-label="Canonical value" value={to} onChange={(e) => update(index, from, e.target.value)} />
          )}
          <button className="icon" aria-label={`Remove mapping for ${from}`} onClick={() => remove(index)}>
            ×
          </button>
        </div>
      ))}
      <button className="link" onClick={() => onChange({ ...mapping, '': enumValues?.[0] ?? '' })}>
        + add value
      </button>
    </div>
  )
}
