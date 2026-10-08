import { formatDate, formatDateTime } from '../format'
import type { EncounterSummary, PatientSummary, VitalSummary } from '../types'

interface Props {
  patient: PatientSummary
  encounters: EncounterSummary[]
  vitals: VitalSummary[]
}

/** Patient identity plus the vitals context the spec calls for. */
export function PatientHeader({ patient, encounters, vitals }: Props) {
  const current =
    encounters.find((e) => e.classCode.toUpperCase() === 'IMP' && !e.end) ??
    encounters.find((e) => e.classCode.toUpperCase() === 'IMP') ??
    encounters[0]

  return (
    <section className="patient" aria-label="Patient summary">
      <div className="patient__identity">
        <h2 className="patient__name">{patient.name}</h2>
        <dl className="patient__facts">
          <div>
            <dt>Patient ID</dt>
            <dd className="mono">{patient.id || '—'}</dd>
          </div>
          <div>
            <dt>Born</dt>
            <dd>{formatDate(patient.birthDate) }</dd>
          </div>
          <div>
            <dt>Sex</dt>
            <dd>{patient.gender || '—'}</dd>
          </div>
          {current && (
            <div>
              <dt>Admitted</dt>
              <dd>
                {formatDateTime(current.start)}
                {current.end ? ` · discharged ${formatDateTime(current.end)}` : ' · still admitted'}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {vitals.length > 0 && (
        <div className="patient__vitals">
          <h3 className="patient__vitals-title">Latest vitals</h3>
          <ul>
            {vitals.slice(0, 4).map((vital) => (
              <li key={vital.id}>
                <span className="vital__label">{vital.label}</span>
                <span className="vital__value">{vital.value || '—'}</span>
                <span className="vital__time">{formatDateTime(vital.recordedAt)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
