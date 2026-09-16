import type { StoredSpec } from '../types/api'

export function HistoryPanel({ history }: { history: StoredSpec[] }) {
  if (history.length === 0) return null

  return (
    <section className="panel">
      <h2>Versions</h2>
      <ol className="history">
        {history.map((entry) => (
          <li key={entry.id}>
            <div>
              <strong>v{entry.version}</strong>{' '}
              <span className="muted">
                · {entry.created_by === 'human' ? 'reviewer' : 'proposer'}
                {entry.created_at && ` · ${new Date(entry.created_at).toLocaleString()}`} ·{' '}
                <code>{entry.content_hash.slice(0, 8)}</code>
              </span>
            </div>
            <p className="muted small">{describeChanges(entry)}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}

function describeChanges(entry: StoredSpec): string {
  const changes = entry.changes ?? []
  if (entry.parent_id === null) return 'First proposal.'
  if (changes.length === 0) return 'No field changes.'
  return changes
    .map((change) => {
      if (change.before === change.after) return `${change.target_field}: steps changed`
      const moved = `${change.target_field}: ${change.before ?? 'not mapped'} → ${change.after ?? 'not mapped'}`
      return change.transforms_changed ? `${moved}, steps changed` : moved
    })
    .join('; ')
}
