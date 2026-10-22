import { useMemo, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { ApiError, useApi, useResource } from "../api/client";
import type { Claim, Policy, PreviewResult, PurposeRules, Referral, RequestDetail } from "../api/types";
import { can, useMe } from "../auth/session";
import { CodeList, Empty, ErrorState, FieldLedger, Loading, Notice, PageHeader, StatusBadge } from "../components/ui";
import { fieldLabel } from "../labels";

type Hint = "allowed" | "review" | "never" | "unneeded";

function hintFor(rules: PurposeRules, field: string): Hint {
  if (rules.fields.allowed.includes(field)) return "allowed";
  if (rules.fields.review_required.includes(field)) return "review";
  if (rules.fields.hard_denied.includes(field) || rules.reject_if_requested.includes(field)) return "never";
  return "unneeded";
}

const HINT_TEXT: Record<Hint, string> = {
  allowed: "Usually released for this purpose",
  review: "Needs privacy review",
  never: "Never shared for this purpose",
  unneeded: "Not needed for this purpose",
};

export function NewRequestPage() {
  const me = useMe();
  const policy = useResource<Policy>("/policy");
  if (me.role === "government_officer") return <Navigate to="/statistics" replace />;
  if (!can(me, "requests:create")) {
    return <ErrorState error={new ApiError(403, { error: { code: "FORBIDDEN", message: "Your role cannot create data-sharing requests." } })} />;
  }
  if (policy.loading) return <Loading />;
  if (policy.error || !policy.data) return <ErrorState error={policy.error ?? new Error("Policy unavailable")} onRetry={policy.reload} />;
  return <RequestForm policy={policy.data} />;
}

function RequestForm({ policy }: { policy: Policy }) {
  const me = useMe();
  const api = useApi();
  const navigate = useNavigate();
  const purposes = Object.entries(policy.document.purposes).filter(([, p]) => p.scope === "patient" && p.allowed_roles.includes(me.role));
  const [purpose, setPurpose] = useState(purposes[0]?.[0] ?? "");
  const rules = policy.document.purposes[purpose];
  const isClaim = rules?.authorization === "insurance_claim";
  const claims = useResource<Claim[]>(isClaim ? "/claims" : null);
  const referrals = useResource<Referral[]>(rules?.authorization === "referral" ? "/referrals" : null);
  const [target, setTarget] = useState("");
  const [fields, setFields] = useState<string[]>([]);
  const [justification, setJustification] = useState("");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<"preview" | "submit" | null>(null);
  const [touched, setTouched] = useState(false);

  const problems = useMemo(() => {
    const p: string[] = [];
    if (!target) p.push(isClaim ? "Choose the claim this request is for." : "Choose the referral this request is for.");
    if (fields.length === 0) p.push("Select at least one field.");
    if (justification.trim().length < 10) p.push("Explain why you need this data (at least 10 characters).");
    return p;
  }, [target, fields, justification, isClaim]);

  if (!rules) return <Empty title="No request purposes are available to your role" />;

  const body = () => ({
    purpose,
    ...(isClaim ? { claim_reference: target } : { referral_reference: target }),
    requested_fields: fields,
    justification: justification.trim(),
  });

  const toggle = (f: string) => {
    setPreview(null);
    setFields((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));
  };

  const run = async (kind: "preview" | "submit") => {
    setTouched(true);
    setError(null);
    if (problems.length) return;
    setBusy(kind);
    try {
      if (kind === "preview") {
        setPreview(await api<PreviewResult>("/requests/preview", { method: "POST", body: body() }));
      } else {
        const res = await api<{ request: RequestDetail }>("/requests", { method: "POST", body: body() });
        navigate(`/requests/${res.request.id}`, { state: { created: true } });
      }
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, null));
    } finally {
      setBusy(null);
    }
  };

  const onSubmit = (e: FormEvent) => { e.preventDefault(); void run("submit"); };
  const targets = isClaim ? claims : referrals;

  return (
    <>
      <PageHeader title="New data-sharing request"
        lead="Ask only for what the purpose needs. The server evaluates every field against the active policy; nothing on this page decides access." />
      <form className="form-grid" onSubmit={onSubmit} noValidate>
        <div className="form-main">
          <fieldset className="panel">
            <legend>Purpose and basis</legend>
            <label className="field">
              <span>Purpose</span>
              <select value={purpose} onChange={(e) => { setPurpose(e.target.value); setTarget(""); setFields([]); setPreview(null); }}>
                {purposes.map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
              </select>
              <small>{rules.description}</small>
            </label>
            <label className="field">
              <span>{isClaim ? "Insurance claim" : "Referral"}</span>
              {targets.loading ? <Loading /> : targets.error ? <ErrorState error={targets.error} /> : (
                <select value={target} aria-invalid={touched && !target} onChange={(e) => { setTarget(e.target.value); setPreview(null); }}>
                  <option value="">{isClaim ? "Choose a claim lodged with your organisation" : "Choose a referral to your organisation"}</option>
                  {isClaim && claims.data?.map((c) => (
                    <option key={c.claim_reference} value={c.claim_reference}>
                      {c.claim_reference}: {c.patient_reference} ({c.patient_alias}), {c.custodian_org}, ${c.amount_claimed}
                    </option>
                  ))}
                  {!isClaim && referrals.data?.map((r) => (
                    <option key={r.referral_reference} value={r.referral_reference} disabled={r.status !== "active"}>
                      {r.referral_reference}: {r.patient_reference} ({r.patient_alias}) from {r.from_org}
                    </option>
                  ))}
                </select>
              )}
              <small>
                {rules.consent === "required"
                  ? "The patient's recorded consent for this purpose is checked on the server."
                  : "The active referral is the basis for sharing; a recorded refusal blocks it."}
              </small>
            </label>
          </fieldset>

          <fieldset className="panel">
            <legend>Fields</legend>
            <p className="muted small">Hints come from the published policy. The server makes the decision.</p>
            <ul className="field-picker">
              {policy.fields.map((f) => {
                const h = hintFor(rules, f.id);
                const id = `f-${f.id}`;
                return (
                  <li key={f.id} className={`pick pick-${h}`}>
                    <input id={id} type="checkbox" checked={fields.includes(f.id)} onChange={() => toggle(f.id)} />
                    <label htmlFor={id}>
                      <span className="pick-name">{f.label}</span>
                      <span className="pick-hint">{HINT_TEXT[h]}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>

          <fieldset className="panel">
            <legend>Justification</legend>
            <label className="field">
              <span>Why do you need this data?</span>
              <textarea rows={4} maxLength={2000} value={justification} aria-invalid={touched && justification.trim().length < 10}
                onChange={(e) => setJustification(e.target.value)}
                placeholder="For example: assessing claim CLM-M-1001 for an inpatient admission against the member's hospital cover." />
              <small>{justification.length}/2000. Recorded with the request and shown to reviewers.</small>
            </label>
          </fieldset>
        </div>

        <aside className="form-side">
          <div className="panel sticky">
            <h2 className="panel-title">Summary</h2>
            <dl className="kv">
              <dt>Purpose</dt><dd>{rules.label}</dd>
              <dt>{isClaim ? "Claim" : "Referral"}</dt><dd>{target || <span className="muted">Not chosen</span>}</dd>
              <dt>Fields</dt><dd>{fields.length ? fields.map(fieldLabel).join(", ") : <span className="muted">None selected</span>}</dd>
            </dl>
            {touched && problems.length > 0 && (
              <Notice tone="warning" title="Before you continue">
                <ul className="plain">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
              </Notice>
            )}
            {error && <ApiErrorNotice error={error} />}
            {preview && (
              <div className="preview" aria-live="polite">
                <div className="preview-head">
                  <span>Policy check</span>
                  <StatusBadge status={preview.decision.status} />
                </div>
                <p className="small">{preview.decision.explanation}</p>
                <FieldLedger requested={fields} decisions={preview.decision.field_decisions}
                  pending={preview.decision.status === "pending_review"} caption="If submitted now" />
                <p className="small muted">Policy {preview.decision.policy_version}. Codes: <CodeList codes={preview.decision.policy_codes} /></p>
              </div>
            )}
            <div className="stack-actions">
              <button type="button" className="btn btn-secondary" disabled={busy !== null} onClick={() => void run("preview")}>
                {busy === "preview" ? "Checking…" : "Check against policy"}
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy !== null}>
                {busy === "submit" ? "Submitting…" : "Submit request"}
              </button>
            </div>
            <p className="small muted">Submitting records the request and its decision in the audit trail, including rejected requests.</p>
          </div>
        </aside>
      </form>
      <p className="small"><Link to="/requests">Back to requests</Link></p>
    </>
  );
}

export function ApiErrorNotice({ error }: { error: ApiError }) {
  const errors = (error.details?.errors as { loc: string[]; msg: string }[] | undefined) ?? [];
  return (
    <Notice tone="danger" title={error.status === 403 ? "Not permitted" : error.status === 429 ? "Slow down" : "Not accepted"}>
      <p>{error.message}</p>
      {errors.length > 0 && <ul className="plain small">{errors.map((e, i) => <li key={i}>{e.msg}</li>)}</ul>}
      {error.status === 403 && <p className="small">This attempt was recorded in the audit trail.</p>}
    </Notice>
  );
}
