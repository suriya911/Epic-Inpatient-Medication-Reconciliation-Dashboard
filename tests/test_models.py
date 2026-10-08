"""Tests for FHIR R4 parsing.

These cover the shapes Epic actually emits, including the optional and alternate fields
that a naive parser trips over.
"""

from __future__ import annotations

from app.models import (
    Administration,
    Encounter,
    Order,
    Patient,
    VitalSign,
    interval_hours_from_timing,
    parse_dt,
)
from tests.conftest import at


# ---------------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------------


def test_naive_timestamps_are_read_as_utc():
    parsed = parse_dt("2026-09-18T08:00:00")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed == at("2026-09-18T08:00:00")


def test_offset_timestamps_are_normalised_to_utc():
    assert parse_dt("2026-09-18T04:00:00-04:00") == at("2026-09-18T08:00:00")


def test_unparseable_timestamps_return_none_rather_than_raising():
    assert parse_dt("not a date") is None
    assert parse_dt(None) is None
    assert parse_dt("") is None


# ---------------------------------------------------------------------------------
# Timing to interval
# ---------------------------------------------------------------------------------


def test_structured_repeat_wins():
    timing = {"repeat": {"frequency": 2, "period": 1, "periodUnit": "d"}}
    assert interval_hours_from_timing(timing) == 12.0


def test_repeat_handles_hourly_periods():
    timing = {"repeat": {"frequency": 1, "period": 8, "periodUnit": "h"}}
    assert interval_hours_from_timing(timing) == 8.0


def test_shorthand_code_is_the_documented_fallback():
    assert interval_hours_from_timing({"code": {"text": "BID"}}) == 12.0
    assert interval_hours_from_timing({"code": {"text": "Q8H"}}) == 8.0
    assert interval_hours_from_timing({"code": {"coding": [{"code": "QHS"}]}}) == 24.0


def test_shorthand_matching_ignores_case_and_punctuation():
    assert interval_hours_from_timing({"code": {"text": "q6h"}}) == 6.0
    assert interval_hours_from_timing({"code": {"text": "Twice Daily"}}) == 12.0


def test_unknown_timing_returns_none_instead_of_guessing():
    assert interval_hours_from_timing({}) is None
    assert interval_hours_from_timing({"code": {"text": "as directed"}}) is None
    assert interval_hours_from_timing({"repeat": {"frequency": 0, "period": 0,
                                                  "periodUnit": "d"}}) is None


# ---------------------------------------------------------------------------------
# Resource parsing
# ---------------------------------------------------------------------------------


def test_patient_name_prefers_text_then_builds_from_parts():
    assert Patient.from_fhir({"id": "p1", "name": [{"text": "Demo Patient One"}]}).name \
        == "Demo Patient One"
    assert Patient.from_fhir(
        {"id": "p1", "name": [{"given": ["Camila", "Maria"], "family": "Lopez"}]}
    ).name == "Camila Maria Lopez"


def test_patient_without_a_name_does_not_crash():
    assert Patient.from_fhir({"id": "p1"}).name == "Unknown patient"


def test_encounter_parses_class_and_period():
    encounter = Encounter.from_fhir({
        "id": "enc-1",
        "status": "in-progress",
        "class": {"code": "IMP"},
        "type": [{"text": "Inpatient Admission"}],
        "subject": {"reference": "Patient/p1"},
        "period": {"start": "2026-09-18T08:00:00Z"},
    })
    assert encounter.class_code == "IMP"
    assert encounter.patient_id == "p1"
    assert encounter.start == at("2026-09-18T08:00:00")
    assert encounter.end is None
    assert "2026-09-18" in encounter.label


def test_order_parses_dose_timing_and_prn_flag():
    order = Order.from_fhir({
        "id": "mr-1",
        "status": "active",
        "medicationCodeableConcept": {
            "coding": [{"code": "1807510", "display": "Vancomycin"}],
            "text": "Vancomycin 1 g IV",
        },
        "subject": {"reference": "Patient/p1"},
        "encounter": {"reference": "Encounter/enc-1"},
        "authoredOn": "2026-09-18T08:00:00Z",
        "dosageInstruction": [{
            "timing": {"repeat": {"frequency": 1, "period": 12, "periodUnit": "h"},
                       "code": {"text": "Q12H"}},
            "doseAndRate": [{"doseQuantity": {"value": 1, "unit": "g"}}],
        }],
    })
    assert order.medication == "Vancomycin 1 g IV"
    assert order.medication_code == "1807510"
    assert order.interval_hours == 12.0
    assert order.dose_text == "1 g"
    assert order.timing_text == "Q12H"
    assert order.is_prn is False
    assert order.is_live is True


def test_order_falls_back_to_medication_reference_display():
    order = Order.from_fhir({
        "id": "mr-2",
        "status": "active",
        "medicationReference": {"reference": "Medication/m1", "display": "Heparin"},
    })
    assert order.medication == "Heparin"


def test_prn_detected_from_either_as_needed_form():
    boolean_form = Order.from_fhir({
        "id": "mr-3", "status": "active",
        "dosageInstruction": [{"asNeededBoolean": True}],
    })
    concept_form = Order.from_fhir({
        "id": "mr-4", "status": "active",
        "dosageInstruction": [{"asNeededCodeableConcept": {"text": "for pain"}}],
    })
    assert boolean_form.is_prn and concept_form.is_prn


def test_administration_reads_effective_period_when_datetime_absent():
    admin = Administration.from_fhir({
        "id": "ma-1",
        "status": "completed",
        "subject": {"reference": "Patient/p1"},
        "effectivePeriod": {"start": "2026-09-18T08:10:00Z",
                            "end": "2026-09-18T09:10:00Z"},
        "request": {"reference": "MedicationRequest/mr-1"},
    })
    assert admin.given_at == at("2026-09-18T08:10:00")
    assert admin.request_id == "mr-1"
    assert admin.counts_as_given is True


def test_administration_encounter_read_from_context_or_encounter():
    via_context = Administration.from_fhir({"id": "a", "status": "completed",
                                            "context": {"reference": "Encounter/e1"}})
    via_encounter = Administration.from_fhir({"id": "b", "status": "completed",
                                              "encounter": {"reference": "Encounter/e2"}})
    assert via_context.encounter_id == "e1"
    assert via_encounter.encounter_id == "e2"


def test_not_done_administration_does_not_count_as_given():
    assert Administration.from_fhir({"id": "a", "status": "not-done"}).counts_as_given \
        is False


def test_vital_sign_combines_blood_pressure_components():
    vital = VitalSign.from_fhir({
        "id": "obs-1",
        "code": {"text": "Blood pressure"},
        "effectiveDateTime": "2026-09-19T06:00:00Z",
        "component": [
            {"code": {"text": "Systolic"}, "valueQuantity": {"value": 148, "unit": "mm[Hg]"}},
            {"code": {"text": "Diastolic"}, "valueQuantity": {"value": 91, "unit": "mm[Hg]"}},
        ],
    })
    assert vital.value == "148 mm[Hg] / 91 mm[Hg]"
    assert vital.recorded_at == at("2026-09-19T06:00:00")
