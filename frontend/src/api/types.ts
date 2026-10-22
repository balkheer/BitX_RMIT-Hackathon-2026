// Mirrors backend/app/schemas.py. The server is the authority for every value here;
// the UI uses roles and permissions only to decide what to show, never to decide access.

export type RequestStatus = "approved" | "approved_with_restrictions" | "pending_review" | "rejected";
export type Role = "hospital_staff" | "insurance_officer" | "government_officer" | "privacy_reviewer" | "platform_admin";

export interface OrgRef { id: string; name: string; type: string; slug?: string | null }

export interface Me {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  organization: OrgRef;
  permissions: string[];
}

export interface AuditEvent {
  id: number;
  seq: number;
  occurred_at: string;
  actor_display: string | null;
  actor_role: string | null;
  actor_org_id: string | null;
  actor_org_name: string | null;
  action: string;
  outcome: "success" | "denied" | "failure" | "info";
  resource_type: string | null;
  resource_id: string | null;
  request_id: string | null;
  request_number: number | null;
  decision: string | null;
  policy_codes: string[];
  details: Record<string, unknown>;
  event_hash: string;
  prev_hash: string;
  hash_valid: boolean;
}

export interface RequestSummary {
  id: string;
  request_number: number;
  purpose: string;
  purpose_label: string;
  scope_type: "patient" | "aggregate";
  status: RequestStatus;
  requester_name: string;
  requester_org: OrgRef;
  custodian_org: OrgRef | null;
  patient_reference: string | null;
  patient_alias: string | null;
  authorization_reference: string | null;
  requested_fields: string[];
  approved_fields: string[];
  denied_fields: string[];
  review_fields: string[];
  review_required: boolean;
  policy_codes: string[];
  decision_explanation: string;
  policy_version: string;
  created_at: string;
  evaluated_at: string;
  released: boolean;
  can_release: boolean;
}

export interface Review {
  id: string;
  reviewer_name: string;
  reviewer_org: string | null;
  decision: "approve" | "reject";
  justification: string;
  resulting_status: RequestStatus;
  created_at: string;
}

export interface ReleaseSummary { id: string; released_fields: string[]; released_at: string; metadata: Record<string, unknown> }

export interface RequestDetail extends RequestSummary {
  justification: string;
  consent_reference: string | null;
  field_decisions: Record<string, string>;
  review_reasons: string[];
  aggregate_query: { metric: string; group_by: string[]; period_start: string; period_end: string } | null;
  reviews: Review[];
  releases: ReleaseSummary[];
  audit_events: AuditEvent[];
  encounter_sensitive: boolean | null;
  can_review: boolean;
}

export interface PolicyDecision {
  status: RequestStatus;
  approved_fields: string[];
  denied_fields: string[];
  review_fields: string[];
  field_decisions: Record<string, string>;
  review_required: boolean;
  review_reasons: string[];
  policy_codes: string[];
  explanation: string;
  policy_version: string;
  evaluated_at: string;
}

export interface PreviewResult { decision: PolicyDecision; consent_status: string; authorization_reference: string }

export interface AggregateGroup { group: Record<string, string>; value: number | null; suppressed: boolean; suppression_reason: string | null }
export interface AggregateResult {
  metric: string;
  metric_label: string;
  group_by: string[];
  period_start: string;
  period_end: string;
  min_group_size: number;
  groups: AggregateGroup[];
  suppressed_groups: number;
  secondary_suppressions: number;
  notice: string;
}

export interface ReleaseResult {
  request_id: string;
  release_id: string;
  released_at: string;
  policy_version: string;
  scope_type: "patient" | "aggregate";
  released_fields: string[];
  withheld_fields: string[];
  records: Record<string, string | null>[] | null;
  aggregate: AggregateResult | null;
  reevaluation: PolicyDecision;
}

export interface Dashboard {
  scope_description: string;
  total_requests: number;
  pending_review: number;
  approved: number;
  approved_with_restrictions: number;
  rejected: number;
  releases: number;
  review_queue: number | null;
  recent_events: AuditEvent[];
}

export interface PatientSummary { id: string; synthetic_ref: string; display_alias: string; birth_year: number; sex: string; region: string; encounter_count: number }
export interface Encounter {
  encounter_reference: string; treatment_date: string; diagnosis_code: string; diagnosis_description: string;
  diagnosis_category: string; procedure_code: string | null; medication: string | null; clinician_notes: string | null;
  has_sensitive_notes: boolean; sensitive_category: boolean; billing_amount: string;
}
export interface Consent { consent_reference: string; purpose: string; grantee_org: string | null; status: string; excluded_fields: string[]; valid_until: string | null }
export interface PatientDetail extends PatientSummary { custodian_org: string; encounters: Encounter[]; consents: Consent[] }

export interface Claim { claim_reference: string; patient_reference: string; patient_alias: string; policy_number: string; amount_claimed: string; status: string; lodged_at: string; custodian_org: string }
export interface Referral { referral_reference: string; patient_reference: string; patient_alias: string; from_org: string; status: string; created_at: string }
export interface Mandate { mandate_reference: string; description: string; allowed_metrics: string[]; valid_until: string | null; is_active: boolean }

export interface AuditPage { items: AuditEvent[]; total: number; limit: number; offset: number; scope_description: string }
export interface VerifyResult {
  valid: boolean; checked_events: number; head_seq: number | null; head_hash: string | null;
  first_invalid_seq: number | null; problem: string | null; verified_at: string; notice: string;
}

export interface FieldSpec { id: string; label: string; sensitivity: string; description: string }
export interface PurposeRules {
  label: string; description: string; scope: "patient" | "aggregate"; allowed_roles: string[];
  authorization: "insurance_claim" | "referral" | "reporting_mandate"; consent: "required" | "refusal_blocks" | "not_applicable";
  fields: { allowed: string[]; review_required: string[]; hard_denied: string[] };
  reject_if_requested: string[]; max_hard_denied_before_reject: number; sensitive_category_review_fields: string[];
  aggregate: { metrics: string[]; group_by: string[]; min_group_size: number; max_group_dimensions: number; max_period_days: number; secondary_suppression: boolean } | null;
}
export interface PolicyDocument { version: string; purposes: Record<string, PurposeRules> }
export interface Policy {
  version: string; activated_at: string; change_note: string; document: PolicyDocument; fields: FieldSpec[];
  metrics: Record<string, string>; groupings: Record<string, string>; effective_min_group_size: number;
  history: { version: string; is_active: boolean; change_note: string; created_at: string; created_by: string | null }[];
}

export interface AdminUser { id: string; username: string; display_name: string; role: Role; organization: OrgRef; is_active: boolean; identity_linked: boolean; last_seen_at: string | null; created_at: string }
export interface AdminOrg { id: string; slug: string; name: string; type: string; is_active: boolean; user_count: number }

export interface ApiErrorBody { error: { code: string; message: string; correlation_id?: string | null; details?: Record<string, unknown> | null } }
