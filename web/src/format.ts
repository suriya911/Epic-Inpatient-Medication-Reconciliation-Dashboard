/** Shared formatting helpers.

Clinical timestamps are rendered in UTC on purpose: the record is in UTC, and silently
shifting times into the viewer's local zone would change what a "missed 08:00 dose"
means depending on who opens the page.
*/

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'UTC',
})

const DATE_ONLY = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return '—'
  return `${DATE_TIME.format(value)} UTC`
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return '—'
  return DATE_ONLY.format(value)
}

/** Render a signed minute offset as human text: 10 minutes late, 20 minutes early. */
export function formatDelay(minutes: number | null): string {
  if (minutes === null || minutes === undefined) return '—'
  const rounded = Math.round(minutes)
  if (rounded === 0) return 'on the minute'
  const magnitude = Math.abs(rounded)
  const text = magnitude >= 120 ? `${(magnitude / 60).toFixed(1)} h` : `${magnitude} min`
  return rounded > 0 ? `${text} late` : `${text} early`
}

/** Convert an ISO instant into the value a datetime-local input expects (UTC-based). */
export function toDateTimeLocal(iso: string): string {
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  return value.toISOString().slice(0, 16)
}

/** Convert a datetime-local input value back into an ISO instant, read as UTC. */
export function fromDateTimeLocal(value: string): string {
  if (!value) return ''
  return `${value}:00Z`
}
