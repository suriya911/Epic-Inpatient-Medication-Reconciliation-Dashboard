import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, fetchPatients, fetchReconciliation } from './api'
import { Controls } from './components/Controls'
import { PatientHeader } from './components/PatientHeader'
import { ProvenanceBanner } from './components/ProvenanceBanner'
import { ReconciliationTable } from './components/ReconciliationTable'
import { SummaryTiles } from './components/SummaryTiles'
import type { DoseStatus, PatientSummary, ReconciliationResponse } from './types'

const DEFAULT_GRACE = 60

export default function App() {
  const [patients, setPatients] = useState<PatientSummary[]>([])
  const [patientId, setPatientId] = useState('')
  const [encounterId, setEncounterId] = useState('')
  const [statusFilter, setStatusFilter] = useState<DoseStatus | 'ALL'>('ALL')
  const [search, setSearch] = useState('')
  const [asOfOverride, setAsOfOverride] = useState('')
  const [graceMinutes, setGraceMinutes] = useState(DEFAULT_GRACE)

  const [data, setData] = useState<ReconciliationResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load the patient picker once, then let the reconciliation effect do the rest.
  useEffect(() => {
    let cancelled = false
    fetchPatients()
      .then((response) => {
        if (cancelled) return
        setPatients(response.patients)
        setPatientId((current) => current || response.defaultPatientId)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(cause instanceof ApiError ? cause.message : 'Could not load the patient list.')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!patientId) return
    let cancelled = false
    // Entering the loading state is the point of this effect: the fetch is the external
    // system being synchronized with, and the spinner must show before it resolves.
    // oxlint-disable-next-line react/set-state-in-effect
    setLoading(true)
    setError(null)

    fetchReconciliation({
      patient: patientId,
      encounter: encounterId || undefined,
      as_of: asOfOverride || undefined,
      grace_minutes: graceMinutes,
    })
      .then((response) => {
        if (cancelled) return
        setData(response)
        setLoading(false)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(cause instanceof ApiError ? cause.message : 'Something went wrong.')
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [patientId, encounterId, asOfOverride, graceMinutes])

  // Status and free-text filtering happen client-side so they feel instant; the
  // server-side filters stay available for direct API use.
  const visibleRows = useMemo(() => {
    if (!data) return []
    const needle = search.trim().toLowerCase()
    return data.rows.filter((row) => {
      if (statusFilter !== 'ALL' && row.status !== statusFilter) return false
      if (needle && !row.medication.toLowerCase().includes(needle)) return false
      return true
    })
  }, [data, statusFilter, search])

  const handlePatientChange = useCallback((id: string) => {
    setPatientId(id)
    // Every other filter is scoped to the patient we are leaving. Keeping them would
    // open the new chart on an empty table (a status with no rows, an encounter that
    // belongs to someone else), which reads as a broken dashboard.
    setEncounterId('')
    setAsOfOverride('')
    setStatusFilter('ALL')
  }, [])

  const handleReset = useCallback(() => {
    setEncounterId('')
    setStatusFilter('ALL')
    setSearch('')
    setAsOfOverride('')
    setGraceMinutes(DEFAULT_GRACE)
  }, [])

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1>Inpatient Medication Reconciliation</h1>
          <p className="app__subtitle">
            Reconciling <code>MedicationRequest</code> orders against{' '}
            <code>MedicationAdministration</code> records from Epic&apos;s public FHIR R4 sandbox.
          </p>
        </div>
        <a
          className="app__repo"
          href="https://github.com/suriya911/Epic-Inpatient-Medication-Reconciliation-Dashboard"
          target="_blank"
          rel="noreferrer"
        >
          Source on GitHub
        </a>
      </header>

      {error && (
        <div className="alert" role="alert">
          <strong>Could not load the chart.</strong> {error}
        </div>
      )}

      {data && (
        <>
          <ProvenanceBanner data={data} />
          <PatientHeader
            patient={data.patient}
            encounters={data.encounters}
            vitals={data.vitals}
          />
          <Controls
            patients={patients}
            patientId={patientId}
            onPatientChange={handlePatientChange}
            encounters={data.encounters}
            encounterId={encounterId}
            onEncounterChange={setEncounterId}
            search={search}
            onSearchChange={setSearch}
            asOf={data.asOf}
            asOfOverride={asOfOverride}
            onAsOfChange={setAsOfOverride}
            graceMinutes={graceMinutes}
            onGraceChange={setGraceMinutes}
            onReset={handleReset}
          />
          <SummaryTiles
            summary={data.summary}
            active={statusFilter}
            onSelect={setStatusFilter}
          />
          <p className="result-count" aria-live="polite">
            Showing {visibleRows.length} of {data.rows.length} dose rows
            {loading && <span className="result-count__loading"> · refreshing…</span>}
          </p>
          <ReconciliationTable rows={visibleRows} />
        </>
      )}

      {loading && !data && <p className="loading">Loading the medication chart…</p>}

      <footer className="app__footer">
        <p>
          Self-directed learning project built against Epic&apos;s <strong>public FHIR sandbox</strong>{' '}
          using published test patients. No PHI, no production Epic connection, and no Epic
          certification is claimed.
        </p>
      </footer>
    </div>
  )
}
