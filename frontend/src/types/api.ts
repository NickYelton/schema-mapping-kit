export interface HealthResponse {
  status: string
  llm_provider: string
  catalog: string
  tables: string[]
}
