import { formatDateTime } from '../format'
import type { ReconciliationResponse } from '../types'

interface Props {
  data: ReconciliationResponse
}

/** States plainly where the data came from and which moment it was evaluated against.

Both facts matter for honesty: demo data must never look like live Epic data, and the
as-of time must never look like wall-clock now when it is not.
*/
export function ProvenanceBanner({ data }: Props) {
  const isLive = data.dataSource === 'live'
  return (
    <div className={`banner banner--${isLive ? 'live' : 'demo'}`}>
      <div className="banner__row">
        <span className={`banner__tag banner__tag--${isLive ? 'live' : 'demo'}`}>
          {isLive ? 'Live Epic sandbox' : 'Demo data'}
        </span>
        <span className="banner__text">{data.dataSourceNote}</span>
      </div>
      <div className="banner__row">
        <span className="banner__tag banner__tag--asof">As of</span>
        <span className="banner__text">
          <strong>{formatDateTime(data.asOf)}</strong>
          {data.asOfSource === 'latest-clinical-timestamp' ? (
            <>
              {' '}— the latest clinical timestamp in this record. Sandbox data is historical,
              so evaluating against the current wall-clock time would mark every dose missed.
            </>
          ) : (
            <> — supplied manually to review the chart at an earlier moment.</>
          )}
        </span>
      </div>
    </div>
  )
}
