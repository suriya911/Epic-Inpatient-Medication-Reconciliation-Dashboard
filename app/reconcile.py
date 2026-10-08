"""Medication reconciliation engine.

Pure logic: no network, no clock reads beyond what the caller passes in. Every input
arrives as an argument so the whole engine is unit-testable and deterministic.

The clinical question it answers, per inpatient order: given how often this medication
was supposed to be given, which of those doses actually have an administration record,
and which are late or absent entirely?
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Sequence

from app.models import (
    Administration,
    DoseStatus,
    Encounter,
    Order,
    Patient,
    PatientBundle,
    ReconRow,
    interval_to_timedelta,
)

# Never generate more than this many expected doses for one order. Guards against a
# malformed short interval over a long encounter producing tens of thousands of rows.
MAX_EXPECTED_DOSES = 200

DEFAULT_GRACE = timedelta(minutes=60)
DEFAULT_MISS_MULTIPLIER = 2.0


def resolve_as_of(
    bundle: PatientBundle,
    override: datetime | None = None,
) -> datetime:
    """Pick the timestamp everything is evaluated against.

    Sandbox data is static and historical, so comparing it to wall-clock now would mark
    every dose MISSED and make a working dashboard look broken. The default is instead
    the latest clinical timestamp present in the record, which is the moment the chart
    was last true. Callers can override it to time-travel through the stay.
    """
    if override is not None:
        return override

    candidates: list[datetime] = []
    for admin in bundle.administrations:
        if admin.given_at:
            candidates.append(admin.given_at)
    for encounter in bundle.encounters:
        if encounter.end:
            candidates.append(encounter.end)
        elif encounter.start:
            candidates.append(encounter.start)
    for order in bundle.orders:
        if order.validity_end:
            candidates.append(order.validity_end)
        elif order.authored_on:
            candidates.append(order.authored_on)

    if not candidates:
        # No clinical timestamps at all; wall clock is the only honest answer left.
        return datetime.now(tz=_utc())
    return max(candidates)


def _utc():
    from datetime import timezone

    return timezone.utc


def _order_start(order: Order, encounter: Encounter | None) -> datetime | None:
    """When dosing should begin: the later of the order being written and admission."""
    candidates = [dt for dt in (order.validity_start, order.authored_on) if dt]
    start = min(candidates) if candidates else None
    if encounter and encounter.start:
        start = max(start, encounter.start) if start else encounter.start
    return start


def _order_stop(order: Order, encounter: Encounter | None, as_of: datetime) -> datetime:
    """When dosing should stop being expected: order expiry, discharge, or as-of."""
    stops = [dt for dt in (order.validity_end, encounter.end if encounter else None) if dt]
    stops.append(as_of)
    return min(stops)


def expected_dose_times(
    order: Order,
    encounter: Encounter | None,
    as_of: datetime,
) -> list[datetime]:
    """Build the schedule of doses this order should have produced by as_of."""
    if not order.interval_hours or order.interval_hours <= 0:
        return []

    start = _order_start(order, encounter)
    if start is None:
        return []

    stop = _order_stop(order, encounter, as_of)
    if stop < start:
        return []

    step = interval_to_timedelta(order.interval_hours)
    times: list[datetime] = []
    current = start
    while current <= stop and len(times) < MAX_EXPECTED_DOSES:
        times.append(current)
        current += step
    return times


def administrations_for_order(
    order: Order,
    administrations: Sequence[Administration],
) -> tuple[list[Administration], str]:
    """Find the administrations belonging to one order.

    Prefers the explicit MedicationAdministration.request reference. Falls back to
    medication code within the same encounter, and reports which link was used so the
    UI can say so rather than implying a certainty the data does not support.
    """
    direct = [
        admin for admin in administrations
        if admin.request_id and admin.request_id == order.id
    ]
    if direct:
        return sorted(direct, key=_admin_sort_key), "direct"

    # No reference present: infer by medication code within the same encounter.
    if not order.medication_code:
        return [], "direct"

    inferred = [
        admin for admin in administrations
        if not admin.request_id
        and admin.medication_code
        and admin.medication_code == order.medication_code
        and (not order.encounter_id
             or not admin.encounter_id
             or admin.encounter_id == order.encounter_id)
    ]
    if inferred:
        return sorted(inferred, key=_admin_sort_key), "inferred"
    return [], "direct"


def _admin_sort_key(admin: Administration) -> datetime:
    return admin.given_at or datetime.max.replace(tzinfo=_utc())


def match_doses(
    expected: Sequence[datetime],
    administrations: Sequence[Administration],
    grace: timedelta,
) -> tuple[list[tuple[datetime, Administration | None]], list[Administration]]:
    """Pair each expected dose with at most one administration.

    Greedy nearest-match: each expected time claims the closest unclaimed
    administration inside its window, so a single administration can never satisfy two
    scheduled doses. Returns the pairs plus any administrations left over (doses given
    that no scheduled time accounts for).
    """
    given = [a for a in administrations if a.given_at and a.counts_as_given]
    unclaimed = list(given)
    pairs: list[tuple[datetime, Administration | None]] = []

    for expected_at in expected:
        best: Administration | None = None
        best_delta: timedelta | None = None
        for admin in unclaimed:
            assert admin.given_at is not None
            delta = abs(admin.given_at - expected_at)
            if delta <= grace and (best_delta is None or delta < best_delta):
                best, best_delta = admin, delta
        if best is not None:
            unclaimed.remove(best)
        pairs.append((expected_at, best))

    return pairs, unclaimed


def classify(
    expected_at: datetime,
    administration: Administration | None,
    as_of: datetime,
    interval: timedelta,
    miss_multiplier: float,
) -> DoseStatus:
    """Decide the status of a single expected dose."""
    if administration is not None:
        return DoseStatus.ON_TIME

    lateness = as_of - expected_at
    if lateness <= interval * miss_multiplier:
        return DoseStatus.OVERDUE
    return DoseStatus.MISSED


def reconcile_order(
    order: Order,
    administrations: Sequence[Administration],
    encounter: Encounter | None,
    as_of: datetime,
    patient_name: str,
    grace: timedelta = DEFAULT_GRACE,
    miss_multiplier: float = DEFAULT_MISS_MULTIPLIER,
) -> list[ReconRow]:
    """Reconcile one order into zero or more dose rows."""
    linked, link = administrations_for_order(order, administrations)

    # An administration recorded after the evaluation point has not happened yet from
    # this chart's perspective. Rewinding as_of to review an earlier moment in the stay
    # must not leak later doses back in.
    linked = [a for a in linked if a.given_at is None or a.given_at <= as_of]

    def row(**kwargs) -> ReconRow:
        base = {
            "order_id": order.id,
            "patient_id": order.patient_id,
            "patient_name": patient_name,
            "encounter_id": order.encounter_id,
            "medication": order.medication,
            "dose_text": order.dose_text,
            "timing_text": order.timing_text,
            "ordered_at": order.authored_on,
            "expected_at": None,
            "administered_at": None,
            "link": link,
        }
        base.update(kwargs)
        return ReconRow(**base)

    # PRN orders have no schedule to be late against; report them, never flag them.
    if order.is_prn:
        last = linked[-1] if linked else None
        return [row(
            status=DoseStatus.PRN,
            administered_at=last.given_at if last else None,
            note="As-needed order; not schedule-evaluated.",
        )]

    # Orders that are finished, cancelled, or draft are not owed further doses.
    if not order.is_live:
        last = linked[-1] if linked else None
        return [row(
            status=DoseStatus.INACTIVE,
            administered_at=last.given_at if last else None,
            note=f"Order status '{order.status}'; not schedule-evaluated.",
        )]

    expected = expected_dose_times(order, encounter, as_of)

    # No parseable interval: surface the order honestly instead of guessing a schedule.
    if not expected:
        last = linked[-1] if linked else None
        note = ("No dosing interval could be derived from the order timing."
                if not order.interval_hours
                else "No doses were due in this window.")
        return [row(
            status=DoseStatus.UNSCHEDULED,
            administered_at=last.given_at if last else None,
            note=note,
        )]

    interval = interval_to_timedelta(order.interval_hours or 0)
    effective_grace = min(grace, interval / 2) if interval > timedelta(0) else grace
    pairs, extras = match_doses(expected, linked, effective_grace)

    rows: list[ReconRow] = []
    for expected_at, admin in pairs:
        status = classify(expected_at, admin, as_of, interval, miss_multiplier)
        delay = None
        if admin and admin.given_at:
            delay = (admin.given_at - expected_at).total_seconds() / 60.0
        rows.append(row(
            status=status,
            expected_at=expected_at,
            administered_at=admin.given_at if admin else None,
            delay_minutes=delay,
            dose_text=(admin.dose_text or order.dose_text) if admin else order.dose_text,
        ))

    # Doses given outside any expected window still belong on the chart. They are not
    # ON_TIME (they demonstrably were not), but they are not a missing dose either, so
    # they are reported as given-but-off-schedule.
    for admin in extras:
        rows.append(row(
            status=DoseStatus.UNSCHEDULED,
            expected_at=None,
            administered_at=admin.given_at,
            dose_text=admin.dose_text or order.dose_text,
            note="Administered outside the expected dosing window (unscheduled dose).",
        ))

    return rows


def reconcile_bundle(
    bundle: PatientBundle,
    as_of: datetime | None = None,
    grace: timedelta = DEFAULT_GRACE,
    miss_multiplier: float = DEFAULT_MISS_MULTIPLIER,
    inpatient_only: bool = True,
) -> tuple[list[ReconRow], datetime]:
    """Reconcile every order in a patient bundle.

    Returns the rows sorted worst-status-first, plus the as-of timestamp actually used
    so the caller can display it.
    """
    effective_as_of = resolve_as_of(bundle, as_of)

    encounters = {enc.id: enc for enc in bundle.encounters}
    if inpatient_only:
        inpatient_ids = {
            enc.id for enc in bundle.encounters
            if enc.class_code.upper() in {"IMP", "ACUTE", "NONAC", "EMER"}
        }
    else:
        inpatient_ids = set(encounters)

    rows: list[ReconRow] = []
    for order in bundle.orders:
        # Keep orders tied to an inpatient encounter, plus any order with no encounter
        # link at all (the sandbox is inconsistent about populating it).
        if inpatient_only and order.encounter_id and order.encounter_id not in inpatient_ids:
            continue
        encounter = encounters.get(order.encounter_id)
        rows.extend(reconcile_order(
            order=order,
            administrations=bundle.administrations,
            encounter=encounter,
            as_of=effective_as_of,
            patient_name=bundle.patient.name,
            grace=grace,
            miss_multiplier=miss_multiplier,
        ))

    rows.sort(key=_row_sort_key)
    return rows, effective_as_of


def _row_sort_key(row: ReconRow) -> tuple[int, float, str]:
    from app.models import STATUS_SEVERITY

    when = row.expected_at or row.administered_at or row.ordered_at
    return (
        STATUS_SEVERITY[row.status],
        -(when.timestamp() if when else 0.0),
        row.medication,
    )


def summarize(rows: Iterable[ReconRow]) -> dict[str, int]:
    """Count rows by status for the dashboard summary tiles."""
    counts = {status.value: 0 for status in DoseStatus}
    total = 0
    for row in rows:
        counts[row.status.value] += 1
        total += 1
    counts["TOTAL"] = total
    return counts
