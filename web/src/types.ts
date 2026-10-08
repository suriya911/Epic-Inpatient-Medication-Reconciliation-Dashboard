/** Shapes returned by the FastAPI service. Mirrors app/routes.py. */

export type DoseStatus =
  | 'ON_TIME'
  | 'OVERDUE'
  | 'MISSED'
  | 'PRN'
  | 'INACTIVE'
  | 'UNSCHEDULED'

export interface ReconRow {
  orderId: string
  patientId: string
  patientName: string
  encounterId: string
  medication: string
  doseText: string
  timingText: string
  orderedAt: string | null
  expectedAt: string | null
  administeredAt: string | null
  status: DoseStatus
  delayMinutes: number | null
  /** 'direct' when linked via MedicationAdministration.request, 'inferred' when matched by code. */
  link: 'direct' | 'inferred'
  note: string
  severity: number
}

export interface EncounterSummary {
  id: string
  label: string
  status: string
  classCode: string
  start: string | null
  end: string | null
}

export interface VitalSummary {
  id: string
  label: string
  value: string
  recordedAt: string | null
}

export interface PatientSummary {
  id: string
  name: string
  birthDate: string
  gender: string
}

export type StatusCounts = Record<DoseStatus | 'TOTAL', number>

export interface ReconciliationResponse {
  patient: PatientSummary
  encounters: EncounterSummary[]
  vitals: VitalSummary[]
  rows: ReconRow[]
  summary: StatusCounts
  asOf: string
  asOfSource: 'override' | 'latest-clinical-timestamp'
  dataSource: 'live' | 'fixtures'
  dataSourceNote: string
  settings: {
    graceMinutes: number
    missMultiplier: number
    inpatientOnly: boolean
  }
}

export interface PatientsResponse {
  patients: PatientSummary[]
  defaultPatientId: string
}

/** Presentation metadata per status. Colour is never the only signal: each has a label. */
export const STATUS_META: Record<DoseStatus, { label: string; tone: string; hint: string }> = {
  MISSED: {
    label: 'Missed',
    tone: 'missed',
    hint: 'No administration recorded, and the dose is long past due.',
  },
  OVERDUE: {
    label: 'Overdue',
    tone: 'overdue',
    hint: 'Dose was due and has no administration record yet.',
  },
  ON_TIME: {
    label: 'On time',
    tone: 'ontime',
    hint: 'Administered within the grace window of the expected time.',
  },
  UNSCHEDULED: {
    label: 'Unscheduled',
    tone: 'neutral',
    hint: 'No dosing interval could be derived, or the dose fell outside every expected window.',
  },
  PRN: {
    label: 'PRN',
    tone: 'neutral',
    hint: 'As-needed order. Not evaluated against a schedule.',
  },
  INACTIVE: {
    label: 'Inactive',
    tone: 'muted',
    hint: 'Order is no longer active, so no further doses are expected.',
  },
}

/** Statuses shown as summary tiles, in clinical priority order. */
export const TILE_ORDER: DoseStatus[] = ['MISSED', 'OVERDUE', 'ON_TIME', 'PRN']
