import { fromDateTimeLocal, toDateTimeLocal } from '../format'
import type { EncounterSummary, PatientSummary } from '../types'

interface Props {
  patients: PatientSummary[]
  patientId: string
  onPatientChange: (id: string) => void

  encounters: EncounterSummary[]
  encounterId: string
  onEncounterChange: (id: string) => void

  search: string
  onSearchChange: (value: string) => void

  asOf: string
  asOfOverride: string
  onAsOfChange: (iso: string) => void

  graceMinutes: number
  onGraceChange: (minutes: number) => void

  onReset: () => void
}

export function Controls(props: Props) {
  const inpatient = props.encounters.filter((e) => e.classCode.toUpperCase() === 'IMP')
  const encounterOptions = inpatient.length > 0 ? inpatient : props.encounters

  return (
    <section className="controls" aria-label="Dashboard filters">
      <label className="control">
        <span className="control__label">Patient</span>
        <select
          value={props.patientId}
          onChange={(event) => props.onPatientChange(event.target.value)}
        >
          {props.patients.map((patient) => (
            <option key={patient.id} value={patient.id}>
              {patient.name}
            </option>
          ))}
        </select>
      </label>

      <label className="control">
        <span className="control__label">Encounter</span>
        <select
          value={props.encounterId}
          onChange={(event) => props.onEncounterChange(event.target.value)}
        >
          <option value="">All inpatient encounters</option>
          {encounterOptions.map((encounter) => (
            <option key={encounter.id} value={encounter.id}>
              {encounter.label}
            </option>
          ))}
        </select>
      </label>

      <label className="control control--grow">
        <span className="control__label">Medication</span>
        <input
          type="search"
          placeholder="Filter by name, e.g. vancomycin"
          value={props.search}
          onChange={(event) => props.onSearchChange(event.target.value)}
        />
      </label>

      <label className="control">
        <span className="control__label">
          Evaluate as of
          <span
            className="control__hint"
            title="Move this back to see the chart as it stood at an earlier point in the stay."
          >
            ?
          </span>
        </span>
        <input
          type="datetime-local"
          value={toDateTimeLocal(props.asOfOverride || props.asOf)}
          onChange={(event) => props.onAsOfChange(fromDateTimeLocal(event.target.value))}
        />
      </label>

      <label className="control control--narrow">
        <span className="control__label">
          Grace
          <span
            className="control__hint"
            title="How late a dose may be and still count as on time. Capped at half the dosing interval."
          >
            ?
          </span>
        </span>
        <input
          type="number"
          min={0}
          max={720}
          step={15}
          value={props.graceMinutes}
          onChange={(event) => props.onGraceChange(Number(event.target.value))}
        />
      </label>

      <button type="button" className="control__reset" onClick={props.onReset}>
        Reset
      </button>
    </section>
  )
}
