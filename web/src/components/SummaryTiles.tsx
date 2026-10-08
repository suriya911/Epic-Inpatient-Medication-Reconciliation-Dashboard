import { STATUS_META, TILE_ORDER, type DoseStatus, type StatusCounts } from '../types'

interface Props {
  summary: StatusCounts
  active: DoseStatus | 'ALL'
  onSelect: (status: DoseStatus | 'ALL') => void
}

/** Counts by status, doubling as the primary filter control. */
export function SummaryTiles({ summary, active, onSelect }: Props) {
  return (
    <div className="tiles" role="group" aria-label="Filter by dose status">
      <button
        type="button"
        className={`tile tile--all ${active === 'ALL' ? 'tile--active' : ''}`}
        onClick={() => onSelect('ALL')}
        aria-pressed={active === 'ALL'}
      >
        <span className="tile__value">{summary.TOTAL ?? 0}</span>
        <span className="tile__label">All doses</span>
      </button>

      {TILE_ORDER.map((status) => {
        const meta = STATUS_META[status]
        return (
          <button
            key={status}
            type="button"
            className={`tile tile--${meta.tone} ${active === status ? 'tile--active' : ''}`}
            onClick={() => onSelect(status)}
            aria-pressed={active === status}
            title={meta.hint}
          >
            <span className="tile__value">{summary[status] ?? 0}</span>
            <span className="tile__label">{meta.label}</span>
          </button>
        )
      })}
    </div>
  )
}
