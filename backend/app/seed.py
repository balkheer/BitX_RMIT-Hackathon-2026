"""Synthetic dataset. EVERY person, organisation, diagnosis and amount here is fictional.

Organisation names carry "(synthetic)" so they cannot be mistaken for real
entities. Users are created *unlinked*: they have no identity-provider subject
and therefore cannot sign in until an operator links each one to a TideCloak
account with `python -m app.cli link-identity`. There are no passwords or
credentials anywhere in this dataset.

Seeded requests are created through the same service layer and policy engine
as live requests, so their decisions and audit events are genuine evaluations.
"""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import append_event
from app.auth.identity import CurrentUser
from app.models import (
    Encounter,
    InsuranceClaim,
    Organization,
    Patient,
    PatientConsent,
    Referral,
    ReportingMandate,
    User,
)
from app.schemas import AggregateQueryIn, CreateRequestIn
from app.services import requests as svc

ORGS = [
    ("st-aurelia", "St Aurelia General Hospital (synthetic)", "hospital"),
    ("harbourview", "Harbourview Medical Centre (synthetic)", "hospital"),
    ("meridian-insurance", "Meridian Health Insurance (synthetic)", "insurer"),
    ("kestrel-mutual", "Kestrel Mutual Insurance (synthetic)", "insurer"),
    ("public-health-analytics", "Department of Public Health Analytics (synthetic)", "government"),
]

USERS = [
    ("h.nguyen", "Hana Nguyen", "st-aurelia", "hospital_staff"),
    ("r.okafor", "Remi Okafor", "harbourview", "hospital_staff"),
    ("m.chen", "Mei Chen", "st-aurelia", "privacy_reviewer"),
    ("a.patel", "Arjun Patel", "harbourview", "privacy_reviewer"),
    ("j.walker", "Jordan Walker", "meridian-insurance", "insurance_officer"),
    ("s.lim", "Sora Lim", "kestrel-mutual", "insurance_officer"),
    ("t.brennan", "Tess Brennan", "public-health-analytics", "government_officer"),
]

FIRST = ["Aster", "Briar", "Cedar", "Dune", "Ember", "Fen", "Garnet", "Heath", "Iris", "Juniper", "Kestrel",
         "Linden", "Moss", "Nova", "Onyx", "Pike"]
LAST = ["Vale", "Thorne", "Quill", "Marsh", "Rook", "Sable", "Wren", "Ashby"]
REGIONS = ["North", "Central", "Coastal", "Inland"]

# (category, code, description, procedure, medication, sensitive)
DIAGNOSES = {
    "respiratory": [("J18.9", "Community-acquired pneumonia (synthetic)", "PR-1102", "Amoxicillin 500mg"),
                    ("J45.909", "Asthma exacerbation (synthetic)", "PR-1040", "Salbutamol inhaler")],
    "cardiovascular": [("I10", "Essential hypertension review (synthetic)", "PR-2201", "Amlodipine 5mg"),
                       ("I48.91", "Atrial fibrillation (synthetic)", "PR-2290", "Apixaban 5mg")],
    "endocrine": [("E11.9", "Type 2 diabetes review (synthetic)", "PR-3105", "Metformin 1g")],
    "musculoskeletal": [("S82.6", "Ankle fracture (synthetic)", "PR-4410", "Paracetamol 1g"),
                        ("M54.5", "Lower back pain (synthetic)", "PR-4402", "Ibuprofen 400mg")],
    "mental_health": [("F32.1", "Moderate depressive episode (synthetic)", "PR-5501", "Sertraline 50mg")],
    "sexual_health": [("A56.0", "Chlamydial infection (synthetic)", "PR-6602", "Doxycycline 100mg")],
}
CATEGORY_COUNTS = {"respiratory": 10, "cardiovascular": 8, "endocrine": 7, "musculoskeletal": 6,
                   "mental_health": 3, "sexual_health": 2}
SENSITIVE = {"mental_health", "sexual_health"}


def _cu(db: Session, username: str) -> CurrentUser:
    u = db.execute(select(User).where(User.username == username)).scalar_one()
    o = u.organization
    return CurrentUser(id=u.id, username=u.username, display_name=u.display_name, role=u.role, org_id=o.id,
                       org_name=o.name, org_type=o.org_type, org_slug=o.slug)


def seed(db: Session) -> dict[str, int]:
    if db.execute(select(Patient.id).limit(1)).first():
        raise RuntimeError("synthetic data already present")
    rng = random.Random(20261022)  # deterministic dataset
    orgs: dict[str, Organization] = {}
    for slug, name, kind in ORGS:
        o = db.execute(select(Organization).where(Organization.slug == slug)).scalar_one_or_none()
        if o is None:
            o = Organization(slug=slug, name=name, org_type=kind)
            db.add(o)
        orgs[slug] = o
    db.flush()
    for username, display, org_slug, role in USERS:
        db.add(User(username=username, display_name=display, organization_id=orgs[org_slug].id, role=role))
    db.flush()

    append_event(db, actor=None, action="SYNTHETIC_DATA_SEEDED", outcome="info", resource_type="dataset",
                 details={"note": "Fictional dataset loaded. Following seeded events were produced by the seed "
                                  "script acting through the normal service layer.",
                          "organizations": len(ORGS), "users": len(USERS)})

    # Patients: 10 held by St Aurelia, 6 by Harbourview.
    patients: list[Patient] = []
    for i in range(16):
        custodian = orgs["st-aurelia"] if i < 10 else orgs["harbourview"]
        region = REGIONS[0] if i % 7 in (0, 3, 5) else REGIONS[1] if i % 7 in (1, 4) else REGIONS[2]
        p = Patient(synthetic_ref=f"SYN-P-{i + 1:04d}", display_alias=f"{FIRST[i]} {LAST[i % len(LAST)]}",
                    birth_year=1948 + (i * 37) % 55, sex="F" if i % 2 == 0 else "M", region=region,
                    custodian_org_id=custodian.id)
        db.add(p)
        patients.append(p)
    db.flush()

    # Encounters with fixed per-category counts so the aggregate demo shows suppression.
    encounters: list[Encounter] = []
    plan: list[str] = []
    for cat, count in CATEGORY_COUNTS.items():
        plan += [cat] * count
    start = dt.date(2025, 1, 6)
    inland_slots = {5, 22}  # two encounters for an "Inland" patient -> small region group
    for n, (idx, cat) in enumerate(enumerate(plan), start=1):
        if cat == "mental_health":
            p = patients[[1, 11, 4][[i for i, c in enumerate(plan) if c == "mental_health"].index(idx)]]
        elif cat == "sexual_health":
            p = patients[[6, 13][[i for i, c in enumerate(plan) if c == "sexual_health"].index(idx)]]
        else:
            p = patients[idx % 16]
        if idx in inland_slots:
            p = patients[15]
        code, desc, proc, med = rng.choice(DIAGNOSES[cat])
        e = Encounter(
            encounter_reference=f"ENC-{n:05d}", patient_id=p.id, custodian_org_id=p.custodian_org_id,
            treatment_date=start + dt.timedelta(days=(idx * 17) % 600), diagnosis_code=code,
            diagnosis_description=desc, diagnosis_category=cat, procedure_code=proc, medication=med,
            clinician_notes=f"Synthetic clinical note for {desc.lower()}. Follow-up arranged.",
            sensitive_notes=("Synthetic sensitive note: psychosocial history recorded." if cat in SENSITIVE
                             else None),
            sensitive_category=cat in SENSITIVE,
            billing_amount_cents=rng.randint(180, 4200) * 100 + rng.choice([0, 50]),
        )
        db.add(e)
        encounters.append(e)
    patients[15].region = "Inland"
    db.flush()

    def first_encounter(p: Patient, category: str | None = None) -> Encounter:
        for e in encounters:
            if e.patient_id == p.id and (category is None or e.diagnosis_category == category):
                return e
        raise LookupError

    mer, kes = orgs["meridian-insurance"], orgs["kestrel-mutual"]
    claims = [
        ("CLM-M-1001", patients[0], None, mer, "granted", []),          # Workflow A: valid claim
        ("CLM-M-1002", patients[1], "mental_health", mer, "granted", []),  # sensitive -> review
        ("CLM-M-1003", patients[2], None, mer, "withdrawn", []),       # consent withdrawn -> reject
        ("CLM-M-1004", patients[3], None, mer, "granted", ["procedure_code"]),  # consent excludes a field
        ("CLM-M-1005", patients[8], None, mer, "granted", []),         # for excessive-request demo
        ("CLM-K-2001", patients[10], None, kes, "granted", []),
        ("CLM-K-2002", patients[12], None, kes, "none", []),           # no consent recorded
    ]
    for ref, p, claim_cat, insurer, consent_status, excluded in claims:
        e = first_encounter(p, claim_cat)
        db.add(InsuranceClaim(claim_reference=ref, encounter_id=e.id, patient_id=p.id, insurer_org_id=insurer.id,
                              policy_number=f"POL-{ref[-4:]}-{rng.randint(100, 999)}",
                              amount_claimed_cents=e.billing_amount_cents, status="lodged"))
        if consent_status != "none":
            db.add(PatientConsent(consent_reference=f"CNS-{ref[4:]}", patient_id=p.id,
                                  purpose="insurance_claim_assessment", grantee_org_id=insurer.id,
                                  status=consent_status, excluded_fields=excluded,
                                  valid_until=dt.date(2027, 12, 31)))

    sa, hv = orgs["st-aurelia"], orgs["harbourview"]
    referrals = [
        ("REF-3001", patients[5], None, sa, hv),
        ("REF-3002", patients[11], "mental_health", hv, sa),
        ("REF-3003", patients[4], "mental_health", sa, hv),
        ("REF-3004", patients[14], None, hv, sa),
    ]
    for ref, p, ref_cat, frm, to in referrals:
        e = first_encounter(p, ref_cat)
        db.add(Referral(referral_reference=ref, patient_id=p.id, encounter_id=e.id, from_org_id=frm.id,
                        to_org_id=to.id, status="active"))
    # Patient 15 refused continuity-of-care sharing.
    db.add(PatientConsent(consent_reference="CNS-COC-0015", patient_id=patients[14].id,
                          purpose="continuity_of_care", grantee_org_id=None, status="refused",
                          excluded_fields=[]))

    gov = orgs["public-health-analytics"]
    db.add(ReportingMandate(mandate_reference="MAN-PH-2026", organization_id=gov.id,
                            description="Quarterly communicable and chronic disease surveillance (synthetic)",
                            allowed_metrics=["encounter_count", "distinct_patient_count"],
                            valid_until=dt.date(2027, 6, 30), is_active=True))
    db.add(ReportingMandate(mandate_reference="MAN-PH-2024", organization_id=gov.id,
                            description="Expired 2024 reporting mandate (synthetic)",
                            allowed_metrics=["encounter_count"], valid_until=dt.date(2024, 12, 31),
                            is_active=False))
    db.commit()

    # --- Requests through the real service layer --------------------------------
    walker, lim = _cu(db, "j.walker"), _cu(db, "s.lim")
    okafor, nguyen, brennan = _cu(db, "r.okafor"), _cu(db, "h.nguyen"), _cu(db, "t.brennan")

    approved = svc.create(db, walker, CreateRequestIn(
        purpose="insurance_claim_assessment", claim_reference="CLM-M-1001",
        requested_fields=["patient_reference", "claim_reference", "treatment_date", "diagnosis_code",
                          "billing_amount"],
        justification="Assessing hospital admission claim CLM-M-1001 against policy cover."))
    svc.release(db, walker, approved.id)
    svc.create(db, walker, CreateRequestIn(
        purpose="insurance_claim_assessment", claim_reference="CLM-M-1004",
        requested_fields=["patient_reference", "treatment_date", "procedure_code", "billing_amount",
                          "clinician_notes"],
        justification="Claim CLM-M-1004 lodged for outpatient procedure; verifying billed items."))
    svc.create(db, walker, CreateRequestIn(
        purpose="insurance_claim_assessment", claim_reference="CLM-M-1003",
        requested_fields=["patient_reference", "treatment_date", "billing_amount"],
        justification="Routine assessment of claim CLM-M-1003."))
    svc.create(db, lim, CreateRequestIn(
        purpose="insurance_claim_assessment", claim_reference="CLM-K-2001",
        requested_fields=["patient_reference", "full_medical_history", "clinician_notes", "sensitive_notes"],
        justification="Underwriting review requested by claims team for CLM-K-2001."))
    pending = svc.create(db, walker, CreateRequestIn(
        purpose="insurance_claim_assessment", claim_reference="CLM-M-1002",
        requested_fields=["patient_reference", "treatment_date", "diagnosis_code", "medication", "billing_amount"],
        justification="Claim CLM-M-1002 includes a specialist consultation; need diagnosis and medication."))
    assert pending.status == "pending_review"
    svc.create(db, okafor, CreateRequestIn(
        purpose="continuity_of_care", referral_reference="REF-3001",
        requested_fields=["patient_reference", "age_band", "treatment_date", "diagnosis_code",
                          "diagnosis_description", "medication", "clinician_notes"],
        justification="Patient referred for ongoing respiratory management; need recent history."))
    reviewed = svc.create(db, okafor, CreateRequestIn(
        purpose="continuity_of_care", referral_reference="REF-3003",
        requested_fields=["patient_reference", "diagnosis_code", "medication", "sensitive_notes"],
        justification="Transfer of care to Harbourview mental-health service under referral REF-3003."))
    svc.decide_review(db, _cu(db, "m.chen"), reviewed.id, "approve",
                      "Treating team requires psychosocial history for safe transfer of care; referral verified.")
    svc.create(db, nguyen, CreateRequestIn(
        purpose="continuity_of_care", referral_reference="REF-3004",
        requested_fields=["patient_reference", "diagnosis_code"],
        justification="Follow-up care after referral REF-3004."))
    agg = svc.create(db, brennan, CreateRequestIn(
        purpose="public_health_reporting", mandate_reference="MAN-PH-2026",
        aggregate=AggregateQueryIn(metric="encounter_count", group_by=["diagnosis_category"],
                                   period_start=dt.date(2025, 1, 1), period_end=dt.date(2026, 9, 30)),
        justification="Quarterly chronic and communicable disease surveillance report."))
    svc.release(db, brennan, agg.id)

    return {"organizations": len(ORGS), "users": len(USERS), "patients": len(patients),
            "encounters": len(encounters), "claims": len(claims), "referrals": len(referrals)}
