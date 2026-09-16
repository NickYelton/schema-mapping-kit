import type { MappingSpec, TransformOp } from '../types/api'

export const DEFAULT_ARGS: Record<string, Record<string, unknown>> = {
  null_if: { tokens: ['', '-', 'N/A', 'null'] },
  parse_decimal: { locale: 'us' },
  parse_date: { format: '%Y-%m-%d' },
  map_values: { mapping: {} },
  regex_extract: { pattern: '(.*)' },
  default: { value: '' },
}

const DESCRIPTIONS: Record<string, string> = {
  strip: 'trim whitespace',
  lower: 'lower-case',
  upper: 'upper-case',
  strip_currency: 'remove currency symbols',
  parse_percent: 'drop the % sign',
}

export function describeOp(op: TransformOp): string {
  return DESCRIPTIONS[op.op] ?? op.op.replace(/_/g, ' ')
}

export function insertBeforeCast(ops: TransformOp[], op: TransformOp): TransformOp[] {
  const last = ops.length - 1
  if (last >= 0 && ops[last].op === 'cast') return [...ops.slice(0, last), op, ops[last]]
  return [...ops, op]
}

/** Add `raw -> to` to the column's value map, keying it the way earlier steps will have
 * reshaped the value by the time the map sees it. */
export function withValueMapping(ops: TransformOp[], raw: string, to: string): TransformOp[] {
  const index = ops.findIndex((op) => op.op === 'map_values')
  const before = ops.slice(0, index === -1 ? Math.max(ops.length - 1, 0) : index)
  const key = before.reduce((value, op) => {
    if (op.op === 'strip') return value.trim()
    if (op.op === 'lower') return value.toLowerCase()
    if (op.op === 'upper') return value.toUpperCase()
    return value
  }, raw)

  if (index === -1) {
    return insertBeforeCast(ops, { op: 'map_values', args: { mapping: { [key]: to } } })
  }
  const mapping = { ...(ops[index].args.mapping as Record<string, string>), [key]: to }
  return ops.map((op, i) => (i === index ? { ...op, args: { ...op.args, mapping } } : op))
}

export function tokensText(tokens: unknown): string {
  const list = Array.isArray(tokens) ? tokens : []
  return list.map((token) => (token === '' ? '∅' : String(token))).join(', ')
}

export function parseTokens(text: string): string[] {
  const tokens = text
    .split(',')
    .map((token) => token.trim())
    .filter((token) => token.length > 0)
    .map((token) => (token === '∅' ? '' : token))
  return [...new Set(tokens)]
}

/** A comparison key over what a spec decides, matching what the backend's content hash
 * covers — so a reordered map or re-attached provenance does not count as an edit. */
export function decisionKey(spec: MappingSpec): string {
  const decisions = [...spec.mappings]
    .sort((a, b) => a.target_field.localeCompare(b.target_field))
    .map((m) => [m.target_field, m.source_column, m.literal, m.transforms])
  return stable(decisions)
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
      a.localeCompare(b),
    )
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(',')}}`
  }
  return JSON.stringify(value ?? null)
}
