export interface HealthResponse {
  status: string
  phase: number
  phase_label: string
  phase_total: number
  llm_provider: string
  catalog: string
  tables: string[]
}

export interface SampleFile {
  name: string
  kind: string
  bytes: number
  sheets?: string[]
}

export interface SourceSummary {
  source_id: string
  filename: string
  kind: string
  sheet: string | null
  row_count: number
  column_count: number
  sniff: Record<string, unknown>
  columns: string[]
}

export interface TopValue {
  value: string
  count: number
  pct: number
}

export interface PatternHit {
  pattern: string
  count: number
  pct: number
}

export interface DateCandidate {
  format: string
  label: string
  parsed: number
  pct: number
}

export interface NumericStats {
  min: number
  max: number
  mean: number
  stddev: number
}

export interface ColumnProfile {
  name: string
  ordinal: number
  row_count: number
  null_count: number
  null_rate: number
  blank_count: number
  distinct_count: number
  normalized_distinct_count: number
  cardinality_ratio: number
  inferred_type: string
  type_confidence: number
  type_candidates: Record<string, number>
  min_length: number
  max_length: number
  numeric: NumericStats | null
  date_candidates: DateCandidate[]
  date_ambiguous: boolean
  top_values: TopValue[]
  patterns: PatternHit[]
  pattern_coverage: number
  semantics: string[]
  samples: string[]
}

export interface Preview {
  columns: string[]
  rows: (string | number | null)[][]
}

export interface TargetConstraints {
  enum: string[] | null
  pattern: string | null
  min: number | null
  max: number | null
  min_length: number | null
  max_length: number | null
  unique: boolean
}

export interface TargetField {
  name: string
  dtype: string
  nullable: boolean
  description: string
  aliases: string[]
  constraints: TargetConstraints
  examples: string[]
}

export interface TargetSchema {
  name: string
  version: number
  title: string
  description: string
  primary_key: string[]
  fields: TargetField[]
}

export interface SchemaSummary {
  slug: string
  name: string
  version: number
  title: string
  description: string
  field_count: number
  primary_key: string[]
}
