import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { TargetConstraints, TargetSchema } from '../types/api'

/** Render the constraint object as the short phrases a reviewer can scan. */
function constraintChips(c: TargetConstraints): string[] {
  const chips: string[] = []
  if (c.enum) chips.push(c.enum.join(' · '))
  if (c.pattern) chips.push(c.pattern)
  if (c.min !== null && c.max !== null) chips.push(`${c.min}–${c.max}`)
  else if (c.min !== null) chips.push(`≥ ${c.min}`)
  else if (c.max !== null) chips.push(`≤ ${c.max}`)
  if (c.max_length !== null) chips.push(`≤ ${c.max_length} chars`)
  if (c.unique) chips.push('unique')
  return chips
}

export function TargetSchemaPanel() {
  const [schema, setSchema] = useState<TargetSchema | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .schemas()
      .then((list) => (list.length ? api.schema(list[0].slug) : null))
      .then(setSchema)
      .catch((e: Error) => setError(e.message))
  }, [])

  if (error) {
    return (
      <section className="panel">
        <h2>Target schema</h2>
        <p className="error">{error}</p>
      </section>
    )
  }
  if (!schema) return null

  return (
    <section className="panel">
      <h2>
        Target schema
        <span className="muted">
          {' '}
          {schema.name} v{schema.version}
        </span>
      </h2>
      <p className="muted">{schema.description}</p>

      <div className="table-scroll">
        <table className="schema-table">
          <thead>
            <tr>
              <th>Field</th>
              <th>Type</th>
              <th>Required</th>
              <th>Constraints</th>
            </tr>
          </thead>
          <tbody>
            {schema.fields.map((field) => {
              const isKey = schema.primary_key.includes(field.name)
              return (
                <tr key={field.name}>
                  <td>
                    <code>{field.name}</code>
                    {isKey && <span className="tag" title="Part of the primary key"> key</span>}
                    <div className="muted field-desc">{field.description}</div>
                  </td>
                  <td>
                    <span className="type">{field.dtype}</span>
                  </td>
                  <td>{field.nullable ? <span className="muted">optional</span> : 'required'}</td>
                  <td>
                    {constraintChips(field.constraints).map((chip) => (
                      <code key={chip} className="chip">
                        {chip}
                      </code>
                    ))}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
