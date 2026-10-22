import type { RequestStatus, Role } from "./api/types";

export const STATUS_LABEL: Record<RequestStatus, string> = {
  approved: "Approved",
  approved_with_restrictions: "Approved with restrictions",
  pending_review: "Pending review",
  rejected: "Rejected",
};

export const ROLE_LABEL: Record<Role, string> = {
  hospital_staff: "Hospital staff",
  insurance_officer: "Insurance officer",
  government_officer: "Government health officer",
  privacy_reviewer: "Privacy reviewer",
  platform_admin: "Platform administrator",
};

export const FIELD_LABEL: Record<string, string> = {
  patient_reference: "Patient reference",
  claim_reference: "Claim reference",
  age_band: "Age band",
  sex: "Sex",
  region: "Region",
  treatment_date: "Treatment date",
  diagnosis_code: "Diagnosis code",
  diagnosis_description: "Diagnosis description",
  procedure_code: "Procedure code",
  medication: "Medication",
  billing_amount: "Billing amount",
  clinician_notes: "Clinician notes",
  sensitive_notes: "Sensitive notes",
  full_medical_history: "Full medical history",
};

export const fieldLabel = (id: string) => FIELD_LABEL[id] ?? id;

export const ACTION_LABEL: Record<string, string> = {
  REQUEST_SUBMITTED: "Request submitted",
  POLICY_EVALUATED: "Policy evaluated",
  POLICY_PREVIEWED: "Policy previewed",
  POLICY_REEVALUATED: "Policy re-evaluated",
  DATA_RELEASED: "Data released",
  RELEASE_BLOCKED: "Release blocked",
  REVIEW_DECIDED: "Review decided",
  ACCESS_DENIED: "Access denied",
  AUTH_DENIED: "Sign-in refused",
  AUTH_SESSION_STARTED: "Session started",
  AUDIT_VERIFIED: "Audit chain verified",
  POLICY_CHANGED: "Policy changed",
  USER_CREATED: "User created",
  USER_UPDATED: "User updated",
  ORGANIZATION_UPDATED: "Organisation updated",
  IDENTITY_LINKED: "Identity linked",
  PATIENT_RECORD_VIEWED: "Patient record viewed",
  SYNTHETIC_DATA_SEEDED: "Synthetic data loaded",
};

export const actionLabel = (a: string) => ACTION_LABEL[a] ?? a.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const dtf = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const df = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
export const fmtDateTime = (s: string) => dtf.format(new Date(s));
export const fmtDate = (s: string) => df.format(new Date(s.length === 10 ? `${s}T00:00:00` : s));
export const reqNo = (n: number) => `CB-${n}`;

export const DECISION_LABEL: Record<string, string> = {
  approved: "Released",
  "approved:reviewed": "Released after review",
  review: "Awaiting review",
  "denied:hard_deny": "Never shared for this purpose",
  "denied:not_necessary_for_purpose": "Not needed for this purpose",
  "denied:consent_excluded": "Excluded by patient consent",
  "denied:review_rejected": "Declined by reviewer",
  denied: "Withheld",
};
