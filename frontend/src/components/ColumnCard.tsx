import type { ColumnProfile } from '../types/api'

const TAG_HINTS: Record<string, string> = {
  enum: 'Small set of repeating values',
  identifier: 'Nearly unique across rows',
  inconsistent_case: 'Same value spelled with different casing',
  european_decimal: 'Comma decimal separator (1.234,56)',
  currency: 'Carries a currency symbol or code',
  percentage: 'Trailing percent sign',
  date: 'Parses as a date',
  email: 'Email address',
  url: 'URL',
  boolean: 'Two-state value',
}

function pct(value: number) {
  return `${Math.round(value * 100)}%`
}

export function ColumnCard({ profile }: { profile: ColumnProfile }) {
  const nullPct = profile.null_rate
  const topPattern = profile.patterns[0]
  const dates = profile.date_candidates

  return (
    <article className="column-card">
      <div className="column-head">
        <h3>{profile.name}</h3>
        <span className="type">{profile.inferred_type}</span>
        {profile.type_confidence < 1 && (
          <span className="conf">{pct(profile.type_confidence)} parse</span>
        )}
      </div>

      <div className="meter" title={`${profile.null_count} of ${profile.row_count} rows null`}>
        <div className="meter-fill" style={{ width: pct(1 - nullPct) }} />
        <span className="meter-label">
          {nullPct > 0 ? `${pct(nullPct)} null` : 'no nulls'}
        </span>
      </div>

      <dl className="stats">
        <dt>Distinct</dt>
        <dd>
          {profile.distinct_count}
          {profile.normalized_distinct_count < profile.distinct_count && (
            <span className="muted"> ({profile.normalized_distinct_count} ignoring case)</span>
          )}
        </dd>
        {topPattern && (
          <>
            <dt>Pattern</dt>
            <dd>
              <code>{topPattern.pattern}</code>
              <span className="muted"> {pct(topPattern.pct)}</span>
            </dd>
          </>
        )}
        {profile.numeric && (
          <>
            <dt>Range</dt>
            <dd>
              {profile.numeric.min} – {profile.numeric.max}
            </dd>
          </>
        )}
        {dates.length > 0 && (
          <>
            <dt>Date format</dt>
            <dd>
              {dates[0].label}
              {profile.date_ambiguous && (
                <span className="warn"> ambiguous vs {dates[1].label}</span>
              )}
            </dd>
          </>
        )}
      </dl>

      {profile.semantics.length > 0 && (
        <div className="tags">
          {profile.semantics.map((tag) => (
            <span key={tag} className="tag" title={TAG_HINTS[tag] ?? tag}>
              {tag.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}

      <ul className="samples">
        {profile.top_values.slice(0, 4).map((v) => (
          <li key={v.value}>
            <code>{v.value === '' ? '∅' : v.value}</code>
            <span className="muted">×{v.count}</span>
          </li>
        ))}
      </ul>
    </article>
  )
}
