"""Server-defined field catalogue.

Field identifiers are the only way a client can refer to patient data. Anything
not in this catalogue is rejected. Policy documents may only reference these
identifiers, so a policy edit cannot invent a new path to data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Sensitivity = Literal["identifier", "demographic", "clinical", "financial", "sensitive", "bundle"]


@dataclass(frozen=True)
class FieldSpec:
    id: str
    label: str
    sensitivity: Sensitivity
    description: str


FIELD_CATALOG: dict[str, FieldSpec] = {
    f.id: f
    for f in [
        FieldSpec("patient_reference", "Patient reference", "identifier", "Synthetic patient identifier"),
        FieldSpec("claim_reference", "Claim reference", "financial", "Insurer claim number linked to the encounter"),
        FieldSpec("age_band", "Age band", "demographic", "Ten-year age band derived from birth year"),
        FieldSpec("sex", "Sex", "demographic", "Recorded sex"),
        FieldSpec("region", "Region", "demographic", "Coarse residential region"),
        FieldSpec("treatment_date", "Treatment date", "clinical", "Date of the encounter"),
        FieldSpec("diagnosis_code", "Diagnosis code", "clinical", "ICD-10-style code"),
        FieldSpec("diagnosis_description", "Diagnosis description", "clinical", "Free-text diagnosis"),
        FieldSpec("procedure_code", "Procedure code", "clinical", "Procedure or item code"),
        FieldSpec("medication", "Medication", "clinical", "Medications prescribed during the encounter"),
        FieldSpec("billing_amount", "Billing amount", "financial", "Amount billed for the encounter"),
        FieldSpec("clinician_notes", "Clinician notes", "clinical", "Narrative clinical notes"),
        FieldSpec("sensitive_notes", "Sensitive notes", "sensitive", "Mental-health, sexual-health or similar notes"),
        FieldSpec(
            "full_medical_history",
            "Full medical history",
            "bundle",
            "Every encounter held for the patient by the custodian",
        ),
    ]
}

ENCOUNTER_FIELDS = {
    "treatment_date",
    "diagnosis_code",
    "diagnosis_description",
    "procedure_code",
    "medication",
    "billing_amount",
    "clinician_notes",
    "sensitive_notes",
}

AGGREGATE_METRICS: dict[str, str] = {
    "encounter_count": "Number of encounters",
    "distinct_patient_count": "Number of distinct patients",
}

AGGREGATE_GROUPINGS: dict[str, str] = {
    "diagnosis_category": "Diagnosis category",
    "region": "Region",
    "age_band": "Age band",
    "sex": "Sex",
}


def is_known_field(field_id: str) -> bool:
    return field_id in FIELD_CATALOG
