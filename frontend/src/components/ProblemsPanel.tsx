import { useState } from 'react'
import type { ProblemGroup, TargetSchema } from '../types/api'

const SHOWN = 12

type Props = {
  groups: ProblemGroup[]
  schema: TargetSchema
  onMapValue: (field: string, raw: string, to: string) => void
}

export function ProblemsPanel({ groups, schema, onMapValue }: Props) {
  const [expanded, setExpanded] = useState(false)
  if (groups.length === 0) return null

  const visible = expanded ? groups : groups.slice(0, SHOWN)
  return (
    <section className="panel">
      <h2>Rows that would be rejected</h2>
      <ul className="problems">
        {visible.map((group) => {
          const accepted = schema.fields.find((f) => f.name === group.field)?.constraints.enum
          const raw = group.value
          return (
            <li key={group.message} className="problem">
              <span className="problem-count">{group.count}×</span>
              <div className="problem-body">
                <p>{group.message}</p>
                <p className="muted small">{lineText(group)}</p>
              </div>
              {group.kind === 'not_allowed' && raw !== null && accepted && (
                <label className="fix">
                  map to
                  <select
                    aria-label={`Map ${raw} to`}
                    value=""
                    onChange={(e) => e.target.value && onMapValue(group.field, raw, e.target.value)}
                  >
                    <option value="">choose…</option>
                    {accepted.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </li>
          )
        })}
      </ul>
      {groups.length > SHOWN && (
        <button className="link" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show fewer' : `Show all ${groups.length}`}
        </button>
      )}
    </section>
  )
}

function lineText(group: ProblemGroup): string {
  const more = group.count - group.lines.length
  const lines = group.lines.join(', ') + (more > 0 ? ` and ${more} more` : '')
  return `${group.count === 1 ? 'Line' : 'Lines'} ${lines}`
}
