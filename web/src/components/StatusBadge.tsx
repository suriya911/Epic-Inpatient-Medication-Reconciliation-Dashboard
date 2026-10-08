import { STATUS_META, type DoseStatus } from '../types'

interface Props {
  status: DoseStatus
}

/** Status pill. Carries a text label as well as colour so it is readable without it. */
export function StatusBadge({ status }: Props) {
  const meta = STATUS_META[status]
  return (
    <span className={`badge badge--${meta.tone}`} title={meta.hint}>
      <span className="badge__dot" aria-hidden="true" />
      {meta.label}
    </span>
  )
}
