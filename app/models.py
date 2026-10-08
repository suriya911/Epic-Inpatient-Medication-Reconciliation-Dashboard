"""Domain models and FHIR R4 parsing.

The reconciliation engine works on these small dataclasses rather than raw FHIR JSON,
so the logic stays readable and testable without fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from dateutil import parser as date_parser

# Dosing shorthand seen in free-text timing codes, mapped to an interval in hours.
CODE_TO_HOURS: dict[str, float] = {
    "QD": 24, "DAILY": 24, "QDAY": 24, "EVERYDAY": 24,
    "BID": 12, "TWICEDAILY": 12,
    "TID": 8, "THREETIMESDAILY": 8,
    "QID": 6, "FOURTIMESDAILY": 6,
    "Q2H": 2, "Q3H": 3, "Q4H": 4, "Q6H": 6, "Q8H": 8, "Q12H": 12, "Q24H": 24,
    "QHS": 24, "NIGHTLY": 24, "QAM": 24, "QPM": 24,
    "QOD": 48, "EVERYOTHERDAY": 48,
    "WEEKLY": 168, "QWK": 168,
}

# FHIR timing.repeat.periodUnit codes to hours.
PERIOD_UNIT_HOURS: dict[str, float] = {
    "s": 1 / 3600, "min": 1 / 60, "h": 1.0, "d": 24.0, "wk": 168.0, "mo": 720.0, "a": 8760.0,
}

# MedicationRequest.status values that represent an order still expected to be given.
LIVE_ORDER_STATUSES = {"active", "on-hold"}

# MedicationAdministration.status values that count as the dose actually reaching the patient.
GIVEN_ADMIN_STATUSES = {"completed", "in-progress"}


class DoseStatus(str, Enum):
    """Reconciliation outcome for a single expected dose or order."""

    ON_TIME = "ON_TIME"
    OVERDUE = "OVERDUE"
    MISSED = "MISSED"
    PRN = "PRN"
    INACTIVE = "INACTIVE"
    UNSCHEDULED = "UNSCHEDULED"


# Sort order for the UI: worst first.
STATUS_SEVERITY: dict[DoseStatus, int] = {
    DoseStatus.MISSED: 0,
    DoseStatus.OVERDUE: 1,
    DoseStatus.ON_TIME: 2,
    DoseStatus.UNSCHEDULED: 3,
    DoseStatus.PRN: 4,
    DoseStatus.INACTIVE: 5,
}


def parse_dt(value: Any) -> datetime | None:
    """Parse a FHIR dateTime/instant into a timezone-aware UTC datetime.

    FHIR permits dates without an offset; those are read as UTC so every comparison in
    the engine happens on aware datetimes.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = date_parser.isoparse(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _concept_text(concept: dict[str, Any] | None) -> str:
    """Best human label from a CodeableConcept: text, then any coding display/code."""
    if not concept:
        return ""
    if concept.get("text"):
        return str(concept["text"])
    for coding in concept.get("coding", []) or []:
        if coding.get("display"):
            return str(coding["display"])
        if coding.get("code"):
            return str(coding["code"])
    return ""


def _concept_code(concept: dict[str, Any] | None) -> str:
    """First coding code from a CodeableConcept, used for inferred order matching."""
    if not concept:
        return ""
    for coding in concept.get("coding", []) or []:
        if coding.get("code"):
            return str(coding["code"])
    return ""


def _reference_id(reference: dict[str, Any] | None) -> str:
    """Pull the logical id out of a FHIR Reference ('MedicationRequest/abc' -> 'abc')."""
    if not reference:
        return ""
    ref = reference.get("reference") or ""
    if ref:
        return str(ref).rstrip("/").split("/")[-1]
    return str(reference.get("id") or "")


@dataclass
class Patient:
    """Minimal patient demographics for the dashboard header."""

    id: str
    name: str
    birth_date: str = ""
    gender: str = ""

    @classmethod
    def from_fhir(cls, resource: dict[str, Any]) -> "Patient":
        names = resource.get("name") or []
        label = ""
        for entry in names:
            if entry.get("text"):
                label = entry["text"]
                break
            given = " ".join(entry.get("given") or [])
            family = entry.get("family") or ""
            if given or family:
                label = f"{given} {family}".strip()
                break
        return cls(
            id=str(resource.get("id") or ""),
            name=label or "Unknown patient",
            birth_date=str(resource.get("birthDate") or ""),
            gender=str(resource.get("gender") or ""),
        )


@dataclass
class Encounter:
    """An inpatient stay, used to scope and filter orders."""

    id: str
    patient_id: str
    status: str
    start: datetime | None
    end: datetime | None
    class_code: str = ""
    type_text: str = ""

    @property
    def label(self) -> str:
        started = self.start.date().isoformat() if self.start else "unknown start"
        return f"{self.type_text or 'Inpatient encounter'} ({started})"

    @classmethod
    def from_fhir(cls, resource: dict[str, Any]) -> "Encounter":
        period = resource.get("period") or {}
        klass = resource.get("class") or {}
        types = resource.get("type") or []
        return cls(
            id=str(resource.get("id") or ""),
            patient_id=_reference_id(resource.get("subject")),
            status=str(resource.get("status") or ""),
            start=parse_dt(period.get("start")),
            end=parse_dt(period.get("end")),
            class_code=str(klass.get("code") or ""),
            type_text=_concept_text(types[0]) if types else "",
        )


@dataclass
class Order:
    """A MedicationRequest: what was ordered, how often, and from when."""

    id: str
    patient_id: str
    encounter_id: str
    medication: str
    medication_code: str
    status: str
    authored_on: datetime | None
    interval_hours: float | None
    is_prn: bool
    dose_text: str = ""
    timing_text: str = ""
    validity_start: datetime | None = None
    validity_end: datetime | None = None

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_ORDER_STATUSES

    @classmethod
    def from_fhir(cls, resource: dict[str, Any]) -> "Order":
        dosage_list = resource.get("dosageInstruction") or []
        dosage = dosage_list[0] if dosage_list else {}
        timing = dosage.get("timing") or {}
        dispense = resource.get("dispenseRequest") or {}
        validity = dispense.get("validityPeriod") or {}

        med_concept = resource.get("medicationCodeableConcept")
        medication = _concept_text(med_concept)
        if not medication:
            med_ref = resource.get("medicationReference") or {}
            medication = str(med_ref.get("display") or "") or "Unknown medication"

        return cls(
            id=str(resource.get("id") or ""),
            patient_id=_reference_id(resource.get("subject")),
            encounter_id=_reference_id(resource.get("encounter")),
            medication=medication,
            medication_code=_concept_code(med_concept),
            status=str(resource.get("status") or ""),
            authored_on=parse_dt(resource.get("authoredOn")),
            interval_hours=interval_hours_from_timing(timing),
            is_prn=bool(dosage.get("asNeededBoolean")
                        or dosage.get("asNeededCodeableConcept")),
            dose_text=_dose_text(dosage),
            timing_text=_timing_text(timing),
            validity_start=parse_dt(validity.get("start")),
            validity_end=parse_dt(validity.get("end")),
        )


@dataclass
class Administration:
    """A MedicationAdministration: a dose actually given."""

    id: str
    patient_id: str
    encounter_id: str
    request_id: str
    medication_code: str
    medication: str
    status: str
    given_at: datetime | None
    dose_text: str = ""

    @property
    def counts_as_given(self) -> bool:
        return self.status in GIVEN_ADMIN_STATUSES

    @classmethod
    def from_fhir(cls, resource: dict[str, Any]) -> "Administration":
        effective = resource.get("effectiveDateTime")
        if not effective:
            period = resource.get("effectivePeriod") or {}
            effective = period.get("start")

        med_concept = resource.get("medicationCodeableConcept")
        medication = _concept_text(med_concept)
        if not medication:
            med_ref = resource.get("medicationReference") or {}
            medication = str(med_ref.get("display") or "")

        dosage = resource.get("dosage") or {}
        return cls(
            id=str(resource.get("id") or ""),
            patient_id=_reference_id(resource.get("subject")),
            encounter_id=_reference_id(resource.get("context")
                                       or resource.get("encounter")),
            request_id=_reference_id(resource.get("request")),
            medication_code=_concept_code(med_concept),
            medication=medication,
            status=str(resource.get("status") or ""),
            given_at=parse_dt(effective),
            dose_text=_quantity_text(dosage.get("dose")),
        )


@dataclass
class VitalSign:
    """An Observation rendered as a context row beside the medication table."""

    id: str
    label: str
    value: str
    recorded_at: datetime | None

    @classmethod
    def from_fhir(cls, resource: dict[str, Any]) -> "VitalSign":
        value = _quantity_text(resource.get("valueQuantity"))
        if not value:
            components = resource.get("component") or []
            parts = [_quantity_text(c.get("valueQuantity")) for c in components]
            value = " / ".join(p for p in parts if p)
        return cls(
            id=str(resource.get("id") or ""),
            label=_concept_text(resource.get("code")) or "Observation",
            value=value or _concept_text(resource.get("valueCodeableConcept")),
            recorded_at=parse_dt(resource.get("effectiveDateTime")),
        )


@dataclass
class ReconRow:
    """One expected dose (or one unschedulable order) after reconciliation."""

    order_id: str
    patient_id: str
    patient_name: str
    encounter_id: str
    medication: str
    dose_text: str
    timing_text: str
    ordered_at: datetime | None
    expected_at: datetime | None
    administered_at: datetime | None
    status: DoseStatus
    delay_minutes: float | None = None
    link: str = "direct"          # "direct" (via request reference) or "inferred" (by code)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe shape consumed directly by the React table."""
        return {
            "orderId": self.order_id,
            "patientId": self.patient_id,
            "patientName": self.patient_name,
            "encounterId": self.encounter_id,
            "medication": self.medication,
            "doseText": self.dose_text,
            "timingText": self.timing_text,
            "orderedAt": _iso(self.ordered_at),
            "expectedAt": _iso(self.expected_at),
            "administeredAt": _iso(self.administered_at),
            "status": self.status.value,
            "delayMinutes": (round(self.delay_minutes, 1)
                             if self.delay_minutes is not None else None),
            "link": self.link,
            "note": self.note,
            "severity": STATUS_SEVERITY[self.status],
        }


@dataclass
class PatientBundle:
    """Everything fetched for one patient, plus where it came from."""

    patient: Patient
    encounters: list[Encounter] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    administrations: list[Administration] = field(default_factory=list)
    vitals: list[VitalSign] = field(default_factory=list)
    source: str = "live"          # "live" or "fixtures"
    source_note: str = ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _quantity_text(quantity: dict[str, Any] | None) -> str:
    """Render a FHIR Quantity as '5 mg'."""
    if not quantity:
        return ""
    value = quantity.get("value")
    unit = quantity.get("unit") or quantity.get("code") or ""
    if value is None:
        return str(unit)
    formatted = f"{value:g}" if isinstance(value, (int, float)) else str(value)
    return f"{formatted} {unit}".strip()


def _dose_text(dosage: dict[str, Any]) -> str:
    """Prefer an explicit dose quantity, fall back to the dosage free text."""
    for dose_and_rate in dosage.get("doseAndRate") or []:
        text = _quantity_text(dose_and_rate.get("doseQuantity"))
        if text:
            return text
    return str(dosage.get("text") or "")


def _timing_text(timing: dict[str, Any]) -> str:
    """Human label for a timing: its code text, else a rebuilt 'N per period'."""
    code_text = _concept_text(timing.get("code"))
    if code_text:
        return code_text
    repeat = timing.get("repeat") or {}
    frequency = repeat.get("frequency")
    period = repeat.get("period")
    unit = repeat.get("periodUnit")
    if frequency and period and unit:
        return f"{frequency} per {period}{unit}"
    return ""


def interval_hours_from_timing(timing: dict[str, Any]) -> float | None:
    """Derive the dosing interval in hours from a FHIR Timing.

    Structured 'repeat' wins because it is unambiguous; a shorthand code such as BID is
    the documented fallback. Returns None when neither yields an interval, which the
    engine treats as UNSCHEDULED rather than guessing.
    """
    if not timing:
        return None

    repeat = timing.get("repeat") or {}
    frequency = repeat.get("frequency")
    period = repeat.get("period")
    unit = repeat.get("periodUnit")
    if frequency and period and unit in PERIOD_UNIT_HOURS:
        try:
            freq = float(frequency)
            per = float(period)
        except (TypeError, ValueError):
            freq = per = 0.0
        if freq > 0 and per > 0:
            return (per * PERIOD_UNIT_HOURS[unit]) / freq

    # Fall back to shorthand in the timing code (text or coding).
    candidates = [_concept_text(timing.get("code")), _concept_code(timing.get("code"))]
    for candidate in candidates:
        key = "".join(ch for ch in str(candidate).upper() if ch.isalnum())
        if key in CODE_TO_HOURS:
            return CODE_TO_HOURS[key]
    return None


def interval_to_timedelta(hours: float) -> timedelta:
    """Convert an interval in hours to a timedelta."""
    return timedelta(hours=hours)
