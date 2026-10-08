import { formatDateTime, formatDelay } from '../format'
import type { ReconRow } from '../types'
import { StatusBadge } from './StatusBadge'

interface Props {
  rows: ReconRow[]
}

/** The reconciliation chart: one row per expected dose. */
export function ReconciliationTable({ rows }: Props) {
  if (rows.length === 0) {
    return (
      <p className="empty">
        No medication rows match the current filters.
      </p>
    )
  }

  return (
    <div className="table-scroll">
      <table className="recon-table">
        <caption className="sr-only">
          Inpatient medication orders reconciled against administration records
        </caption>
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">Medication</th>
            <th scope="col">Dose</th>
            <th scope="col">Schedule</th>
            <th scope="col">Ordered</th>
            <th scope="col">Expected</th>
            <th scope="col">Administered</th>
            <th scope="col">Variance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.orderId}-${row.expectedAt ?? row.administeredAt ?? index}`}>
              <td>
                <StatusBadge status={row.status} />
              </td>
              <td className="cell--medication">
                {row.medication}
                {row.link === 'inferred' && (
                  <span
                    className="flag"
                    title="Matched to this order by medication code because the administration record carried no order reference."
                  >
                    inferred link
                  </span>
                )}
                {row.note && <span className="cell__note">{row.note}</span>}
              </td>
              <td>{row.doseText || '—'}</td>
              <td>{row.timingText || '—'}</td>
              <td className="cell--time">{formatDateTime(row.orderedAt)}</td>
              <td className="cell--time">{formatDateTime(row.expectedAt)}</td>
              <td className="cell--time">{formatDateTime(row.administeredAt)}</td>
              <td className="cell--time">{formatDelay(row.delayMinutes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
