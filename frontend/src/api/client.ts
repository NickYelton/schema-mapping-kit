import type {
  ColumnProfile,
  HealthResponse,
  Preview,
  SampleFile,
  SourceSummary,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new ApiError(detail || response.statusText, response.status)
  }
  return response.json() as Promise<T>
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
}
