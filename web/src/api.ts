/** Client for the dashboard's own API. Never talks to Epic directly. */

import type { PatientsResponse, ReconciliationResponse } from './types'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function getJson<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== '') url.searchParams.set(key, String(value))
  }

  let response: Response
  try {
    response = await fetch(url.toString(), { headers: { Accept: 'application/json' } })
  } catch {
    // A network-level failure carries no useful detail; say the actionable thing instead.
    throw new ApiError('Could not reach the dashboard API. Is the server running?', 0)
  }

  if (!response.ok) {
    // FastAPI puts the useful message in `detail`; surface it rather than a bare code.
    let detail = `Request failed with status ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body; keep the status message */
    }
    throw new ApiError(detail, response.status)
  }

  return (await response.json()) as T
}

export interface ReconciliationQuery {
  patient?: string
  encounter?: string
  as_of?: string
  grace_minutes?: number
  inpatient_only?: string
  [key: string]: string | number | undefined
}

export function fetchPatients(): Promise<PatientsResponse> {
  return getJson<PatientsResponse>('/api/patients')
}

export function fetchReconciliation(query: ReconciliationQuery): Promise<ReconciliationResponse> {
  return getJson<ReconciliationResponse>('/api/reconciliation', query)
}
