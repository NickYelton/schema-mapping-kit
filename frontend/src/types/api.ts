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

export interface TransformOp {
  op: string
  args: Record<string, unknown>
}

export interface Evidence {
  provider: string
  score: number
  detail: string
}

export interface Candidate {
  target_field: string
  score: number
  transforms: TransformOp[]
  provenance: Evidence[]
}

export interface FieldMapping {
  target_field: string
  source_column: string | null
  literal: string | null
  transforms: TransformOp[]
  confidence: number
  provenance: Evidence[]
  decided_by: 'proposed' | 'human'
  note: string
  alternatives: Candidate[]
}

export interface MappingSpec {
  source_id: string
  target_schema: string
  target_version: number
  mappings: FieldMapping[]
  unmapped_columns: string[]
  version: number
  parent_id: string | null
  created_by: string | null
}

export interface SpecChange {
  target_field: string
  before: string | null
  after: string | null
  transforms_changed: boolean
}

export interface StoredSpec {
  id: string
  source_id: string
  target_schema: string
  target_version: number
  version: number
  parent_id: string | null
  content_hash: string
  created_by: string | null
  created_at: string | null
  spec: MappingSpec
  changes?: SpecChange[]
}

export interface ProviderStatus {
  provider: string
  available: boolean
  detail: string
}

export interface ProposeResponse {
  mapped: number
  total: number
  unmapped_columns: string[]
  providers: ProviderStatus[]
  spec_id: string | null
  version: number | null
  content_hash: string
  spec: MappingSpec
}

export interface ProblemGroup {
  field: string
  column: string | null
  kind: string
  value: string | null
  count: number
  lines: number[]
  message: string
}

export interface FieldSample {
  line: number
  raw: string | null
  value: string | null
}

export interface PreviewResponse {
  content_hash: string
  rows_in: number
  rows_valid: number
  rows_rejected: number
  summary: string
  groups: ProblemGroup[]
  field_problems: Record<string, number>
  samples: Record<string, FieldSample[]>
}

export interface OpSpec {
  op: string
  required: string[]
  optional: string[]
}

export interface Vocabulary {
  ops: OpSpec[]
  date_formats: { format: string; label: string }[]
  decimal_locales: string[]
}

export interface SuggestResponse {
  column: string
  field: string
  transforms: TransformOp[]
}

export interface RunResponse {
  run_id: string
  engine: string
  rows_in: number
  rows_out: number
  rows_valid: number | null
  rows_rejected: number | null
  summary: string | null
  groups: ProblemGroup[]
  output_path: string
  sql_path: string
  python_path: string
  report_path: string | null
  rejections_path: string | null
}

export interface CompiledSource {
  engine: string
  source: string
}
