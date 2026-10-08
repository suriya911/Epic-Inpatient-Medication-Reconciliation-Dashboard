"""Unit tests for the reconciliation engine.

The engine is pure, so every case here is built from plain dataclasses rather than
fixtures. Each status branch in PROJECT_PLAN.md section 6 has at least one test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import Administration, DoseStatus, Encounter, Order, Patient, PatientBundle
from app.reconcile import (
    administrations_for_order,
    classify,
    expected_dose_times,
    match_doses,
    reconcile_bundle,
    reconcile_order,
    resolve_as_of,
    summarize,
)
from tests.conftest import at

ENCOUNTER = Encounter(
    id="enc-1",
    patient_id="pat-1",
    status="in-progress",
    start=at("2026-09-18T08:00:00"),
    end=None,
    class_code="IMP",
    type_text="Inpatient Admission",
)


def make_order(**overrides) -> Order:
    base = {
        "id": "ord-1",
        "patient_id": "pat-1",
        "encounter_id": "enc-1",
        "medication": "Vancomycin 1 g IV",
        "medication_code": "1807510",
        "status": "active",
        "authored_on": at("2026-09-18T08:00:00"),
        "interval_hours": 12.0,
        "is_prn": False,
        "dose_text": "1 g",
        "timing_text": "Q12H",
    }
    base.update(overrides)
    return Order(**base)


def make_admin(given_at: str, **overrides) -> Administration:
    base = {
        "id": f"adm-{given_at}",
        "patient_id": "pat-1",
        "encounter_id": "enc-1",
        "request_id": "ord-1",
        "medication_code": "1807510",
        "medication": "Vancomycin 1 g IV",
        "status": "completed",
        "given_at": at(given_at),
        "dose_text": "1 g",
    }
    base.update(overrides)
    return Administration(**base)


def reconcile(order, admins, as_of, **kwargs):
    return reconcile_order(
        order=order,
        administrations=admins,
        encounter=ENCOUNTER,
        as_of=at(as_of),
        patient_name="Demo Patient One",
        **kwargs,
    )


# ---------------------------------------------------------------------------------
# Expected-schedule construction
# ---------------------------------------------------------------------------------


def test_expected_doses_step_by_interval():
    times = expected_dose_times(make_order(), ENCOUNTER, at("2026-09-19T08:00:00"))
    assert [t.isoformat() for t in times] == [
        "2026-09-18T08:00:00+00:00",
        "2026-09-18T20:00:00+00:00",
        "2026-09-19T08:00:00+00:00",
    ]


def test_schedule_starts_at_admission_when_order_predates_it():
    """An order written before admission cannot owe doses before the patient arrived."""
    order = make_order(authored_on=at("2026-09-17T06:00:00"))
    times = expected_dose_times(order, ENCOUNTER, at("2026-09-18T20:00:00"))
    assert times[0] == at("2026-09-18T08:00:00")


def test_schedule_stops_at_discharge():
    discharged = Encounter(
        id="enc-1", patient_id="pat-1", status="finished",
        start=at("2026-09-18T08:00:00"), end=at("2026-09-18T21:00:00"),
        class_code="IMP",
    )
    times = expected_dose_times(make_order(), discharged, at("2026-09-20T08:00:00"))
    assert times == [at("2026-09-18T08:00:00"), at("2026-09-18T20:00:00")]


def test_no_interval_yields_no_schedule():
    assert expected_dose_times(make_order(interval_hours=None), ENCOUNTER,
                               at("2026-09-19T08:00:00")) == []


def test_expected_doses_are_capped():
    """A pathological one-minute interval must not generate unbounded rows."""
    order = make_order(interval_hours=1 / 60)
    times = expected_dose_times(order, ENCOUNTER, at("2026-12-31T00:00:00"))
    assert len(times) == 200


# ---------------------------------------------------------------------------------
# Status branches
# ---------------------------------------------------------------------------------


def test_on_time_dose_within_grace():
    rows = reconcile(make_order(), [make_admin("2026-09-18T08:10:00")],
                     "2026-09-18T09:00:00")
    assert [r.status for r in rows] == [DoseStatus.ON_TIME]
    assert rows[0].delay_minutes == pytest.approx(10.0)


def test_dose_given_early_is_still_on_time_and_reports_negative_delay():
    rows = reconcile(make_order(), [make_admin("2026-09-18T07:40:00")],
                     "2026-09-18T09:00:00")
    assert rows[0].status is DoseStatus.ON_TIME
    assert rows[0].delay_minutes == pytest.approx(-20.0)


def test_recently_missed_dose_is_overdue():
    """Within miss_multiplier x interval of the expected time: OVERDUE, not MISSED."""
    rows = reconcile(make_order(), [], "2026-09-18T14:00:00")
    assert [r.status for r in rows] == [DoseStatus.OVERDUE]


def test_long_past_dose_is_missed():
    """Beyond 2x the 12h interval, the dose is no longer merely late."""
    rows = reconcile(make_order(), [], "2026-09-19T20:00:00")
    assert rows[0].status is DoseStatus.MISSED
    assert rows[0].expected_at == at("2026-09-18T08:00:00")


def test_order_with_no_administrations_flags_every_due_dose():
    # At 12:00 on the 19th the 08:00 dose from the 18th is 28h late (past 2x12h), so the
    # oldest dose has tipped into MISSED while the later two are still merely OVERDUE.
    rows = reconcile(make_order(), [], "2026-09-19T12:00:00")
    assert len(rows) == 3
    assert {r.status for r in rows} == {DoseStatus.MISSED, DoseStatus.OVERDUE}
    assert all(r.administered_at is None for r in rows)


def test_prn_order_is_never_flagged():
    order = make_order(is_prn=True, interval_hours=6.0)
    rows = reconcile(order, [make_admin("2026-09-18T22:00:00")], "2026-09-20T08:00:00")
    assert [r.status for r in rows] == [DoseStatus.PRN]
    assert rows[0].administered_at == at("2026-09-18T22:00:00")


def test_completed_order_is_inactive():
    order = make_order(status="completed")
    rows = reconcile(order, [make_admin("2026-09-18T08:05:00")], "2026-09-20T08:00:00")
    assert [r.status for r in rows] == [DoseStatus.INACTIVE]


@pytest.mark.parametrize("status", ["cancelled", "stopped", "draft", "entered-in-error"])
def test_non_live_statuses_are_all_inactive(status):
    rows = reconcile(make_order(status=status), [], "2026-09-20T08:00:00")
    assert rows[0].status is DoseStatus.INACTIVE


def test_order_without_parseable_timing_is_unscheduled():
    order = make_order(interval_hours=None, timing_text="")
    rows = reconcile(order, [], "2026-09-19T08:00:00")
    assert [r.status for r in rows] == [DoseStatus.UNSCHEDULED]
    assert "No dosing interval" in rows[0].note


# ---------------------------------------------------------------------------------
# Matching rules
# ---------------------------------------------------------------------------------


def test_one_administration_cannot_satisfy_two_doses():
    """The classic double-count bug: a single dose must claim only its nearest slot."""
    expected = [at("2026-09-18T08:00:00"), at("2026-09-18T08:30:00")]
    admin = make_admin("2026-09-18T08:05:00")
    pairs, extras = match_doses(expected, [admin], timedelta(minutes=60))

    matched = [a for _, a in pairs if a is not None]
    assert len(matched) == 1
    assert pairs[0][1] is admin      # claimed by the nearer expected time
    assert pairs[1][1] is None
    assert extras == []


def test_administration_outside_every_window_is_reported_as_unscheduled_dose():
    rows = reconcile(make_order(), [make_admin("2026-09-18T14:00:00")],
                     "2026-09-18T15:00:00")
    extra = [r for r in rows if "unscheduled dose" in r.note]
    assert len(extra) == 1
    assert extra[0].expected_at is None
    assert extra[0].administered_at == at("2026-09-18T14:00:00")
    # Given, but demonstrably not at a scheduled time: never reported as ON_TIME.
    assert extra[0].status is DoseStatus.UNSCHEDULED
    assert DoseStatus.ON_TIME not in [r.status for r in rows]


def test_administrations_after_the_as_of_point_are_not_visible():
    """Rewinding as_of reviews the chart as it stood then, not with hindsight."""
    admins = [make_admin("2026-09-18T08:05:00"), make_admin("2026-09-18T20:05:00")]

    later = reconcile(make_order(), admins, "2026-09-19T00:00:00")
    assert sum(1 for r in later if r.administered_at) == 2

    earlier = reconcile(make_order(), admins, "2026-09-18T09:00:00")
    assert sum(1 for r in earlier if r.administered_at) == 1
    assert all(r.administered_at is None or r.administered_at <= at("2026-09-18T09:00:00")
               for r in earlier)


def test_prn_row_shows_only_doses_given_before_the_as_of_point():
    order = make_order(is_prn=True, interval_hours=6.0)
    admins = [make_admin("2026-09-18T10:00:00"), make_admin("2026-09-18T22:00:00")]
    rows = reconcile(order, admins, "2026-09-18T12:00:00")
    assert rows[0].administered_at == at("2026-09-18T10:00:00")


def test_grace_never_exceeds_half_the_interval():
    """A 60-minute grace must not swallow a whole dose on a 1-hourly order."""
    order = make_order(interval_hours=1.0)
    rows = reconcile(order, [make_admin("2026-09-18T08:40:00")], "2026-09-18T09:00:00",
                     grace=timedelta(minutes=60))
    first = [r for r in rows if r.expected_at == at("2026-09-18T08:00:00")][0]
    assert first.status is not DoseStatus.ON_TIME


def test_not_given_administration_statuses_do_not_count():
    """A dose recorded as not-done is not a dose given."""
    rows = reconcile(make_order(), [make_admin("2026-09-18T08:05:00", status="not-done")],
                     "2026-09-18T09:00:00")
    assert rows[0].status is DoseStatus.OVERDUE


def test_direct_reference_wins_over_code_inference():
    order = make_order()
    direct = make_admin("2026-09-18T08:05:00")
    loose = make_admin("2026-09-18T08:06:00", id="adm-loose", request_id="")
    linked, link = administrations_for_order(order, [loose, direct])
    assert link == "direct"
    assert linked == [direct]


def test_administration_without_request_reference_is_matched_by_code():
    order = make_order()
    loose = make_admin("2026-09-18T08:05:00", id="adm-loose", request_id="")
    linked, link = administrations_for_order(order, [loose])
    assert link == "inferred"
    assert linked == [loose]


def test_inference_does_not_cross_medications():
    order = make_order(medication_code="1807510")
    other = make_admin("2026-09-18T08:05:00", id="adm-other", request_id="",
                       medication_code="999999")
    linked, _ = administrations_for_order(order, [other])
    assert linked == []


def test_classify_boundary_is_inclusive_of_the_threshold():
    """Exactly 2x interval late is still OVERDUE; one second later is MISSED."""
    expected = at("2026-09-18T08:00:00")
    interval = timedelta(hours=12)
    assert classify(expected, None, at("2026-09-19T08:00:00"), interval, 2.0) \
        is DoseStatus.OVERDUE
    assert classify(expected, None, at("2026-09-19T08:00:01"), interval, 2.0) \
        is DoseStatus.MISSED


# ---------------------------------------------------------------------------------
# As-of resolution
# ---------------------------------------------------------------------------------


def test_as_of_defaults_to_latest_clinical_timestamp_not_wall_clock():
    bundle = PatientBundle(
        patient=Patient(id="pat-1", name="Demo Patient One"),
        encounters=[ENCOUNTER],
        orders=[make_order()],
        administrations=[make_admin("2026-09-18T20:05:00")],
    )
    assert resolve_as_of(bundle) == at("2026-09-18T20:05:00")


def test_as_of_override_is_respected():
    bundle = PatientBundle(
        patient=Patient(id="pat-1", name="Demo Patient One"),
        encounters=[ENCOUNTER],
        administrations=[make_admin("2026-09-18T20:05:00")],
    )
    forced = at("2026-09-17T00:00:00")
    assert resolve_as_of(bundle, forced) == forced


def test_as_of_falls_back_to_wall_clock_when_record_is_empty():
    bundle = PatientBundle(patient=Patient(id="pat-1", name="Nobody"))
    resolved = resolve_as_of(bundle)
    assert resolved.tzinfo is not None


# ---------------------------------------------------------------------------------
# Bundle-level behaviour
# ---------------------------------------------------------------------------------


def test_outpatient_encounters_are_excluded_by_default():
    ambulatory = Encounter(
        id="enc-amb", patient_id="pat-1", status="finished",
        start=at("2026-08-02T14:00:00"), end=at("2026-08-02T14:40:00"),
        class_code="AMB",
    )
    bundle = PatientBundle(
        patient=Patient(id="pat-1", name="Demo Patient One"),
        encounters=[ENCOUNTER, ambulatory],
        orders=[make_order(), make_order(id="ord-amb", encounter_id="enc-amb")],
        administrations=[],
    )
    rows, _ = reconcile_bundle(bundle, as_of=at("2026-09-18T09:00:00"))
    assert {r.order_id for r in rows} == {"ord-1"}

    rows_all, _ = reconcile_bundle(bundle, as_of=at("2026-09-18T09:00:00"),
                                   inpatient_only=False)
    assert {r.order_id for r in rows_all} == {"ord-1", "ord-amb"}


def test_rows_are_sorted_worst_status_first():
    bundle = PatientBundle(
        patient=Patient(id="pat-1", name="Demo Patient One"),
        encounters=[ENCOUNTER],
        orders=[make_order()],
        administrations=[make_admin("2026-09-18T08:05:00")],
    )
    rows, _ = reconcile_bundle(bundle, as_of=at("2026-09-20T00:00:00"))
    severities = [r.to_dict()["severity"] for r in rows]
    assert severities == sorted(severities)
    assert rows[0].status is DoseStatus.MISSED


def test_summarize_counts_every_status_key():
    bundle = PatientBundle(
        patient=Patient(id="pat-1", name="Demo Patient One"),
        encounters=[ENCOUNTER],
        orders=[make_order()],
        administrations=[],
    )
    rows, _ = reconcile_bundle(bundle, as_of=at("2026-09-19T08:00:00"))
    counts = summarize(rows)
    assert counts["TOTAL"] == len(rows)
    assert set(counts) >= {s.value for s in DoseStatus}
