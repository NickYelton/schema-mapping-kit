import type {
  ColumnProfile,
  CompiledSource,
  HealthResponse,
  MappingSpec,
  Preview,
  PreviewResponse,
  ProposeResponse,
  RunResponse,
  SampleFile,
  SchemaSummary,
  SourceSummary,
  StoredSpec,
  SuggestResponse,
  TargetSchema,
  Vocabulary,
} from '../types/api'

const BASE = '/api'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function detailOf(text: string): string {
  let body: { detail?: unknown }
  try {
    body = JSON.parse(text)
  } catch {
    return text
  }
  if (typeof body.detail === 'string') return body.detail
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((item) =>
        item && typeof item === 'object' && 'msg' in item ? String(item.msg) : String(item),
      )
      .join('; ')
  }
  return text
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText)
    throw new ApiError(detailOf(text) || response.statusText, response.status)
  }
  return response.json() as Promise<T>
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  samples: () => request<SampleFile[]>('/sources/samples'),

  ingestSample: (name: string, sheet?: string) => {
    const params = new URLSearchParams({ name })
    if (sheet) params.set('sheet', sheet)
    return request<SourceSummary>(`/sources/from-sample?${params}`, { method: 'POST' })
  },

  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<SourceSummary>('/sources', { method: 'POST', body })
  },

  profile: (sourceId: string) => request<ColumnProfile[]>(`/sources/${sourceId}/profile`),

  preview: (sourceId: string, limit = 20) =>
    request<Preview>(`/sources/${sourceId}/preview?limit=${limit}`),

  schemas: () => request<SchemaSummary[]>('/schemas'),

  schema: (name: string, version?: number) =>
    request<TargetSchema>(`/schemas/${name}${version ? `?version=${version}` : ''}`),

  vocabulary: () => request<Vocabulary>('/vocabulary'),

  propose: (sourceId: string, useLlm: boolean) =>
    request<ProposeResponse>(`/sources/${sourceId}/propose?use_llm=${useLlm}`, {
      method: 'POST',
    }),

  spec: (sourceId: string) => request<StoredSpec>(`/sources/${sourceId}/spec`),

  saveSpec: (sourceId: string, spec: MappingSpec) =>
    request<StoredSpec>(`/sources/${sourceId}/spec`, json('PUT', spec)),

  previewSpec: (sourceId: string, spec: MappingSpec, samples = 4) =>
    request<PreviewResponse>(
      `/sources/${sourceId}/spec/preview?samples=${samples}`,
      json('POST', spec),
    ),

  history: (sourceId: string) => request<StoredSpec[]>(`/sources/${sourceId}/spec/history`),

  suggest: (sourceId: string, column: string, field: string, schema: string, version: number) => {
    const params = new URLSearchParams({ column, field, schema, version: String(version) })
    return request<SuggestResponse>(`/sources/${sourceId}/suggest?${params}`)
  },

  run: (sourceId: string, engine: string) =>
    request<RunResponse>(`/sources/${sourceId}/transform?engine=${engine}&limit=0`, {
      method: 'POST',
    }),

  compiled: (sourceId: string, kind: 'sql' | 'python') =>
    request<CompiledSource>(`/sources/${sourceId}/transform/${kind}`),

  rejectionsUrl: (sourceId: string, runId: string) =>
    `${BASE}/sources/${sourceId}/transform/runs/${runId}/rejections.csv`,
}
