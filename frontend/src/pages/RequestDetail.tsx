import { Eye, EyeOff } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError, useApi, useResource } from "../api/client";
import type { AggregateResult, ReleaseResult, RequestDetail } from "../api/types";
import { CodeList, ErrorState, FieldLedger, Loading, Notice, PageHeader, StatusBadge } from "../components/ui";
import { STATUS_LABEL, fieldLabel, fmtDate, fmtDateTime, reqNo } from "../labels";
import { EventList } from "./Audit";
import { ApiErrorNotice } from "./NewRequest";

export function AggregateTable({ result }: { result: AggregateResult }) {
  const max = Math.max(1, ...result.groups.map((g) => g.value ?? 0));
  return (
    <div className="aggregate">
      <p className="small">
        {result.metric_label} by {result.group_by.join(", ").replace(/_/g, " ")}, {fmtDate(result.period_start)} to {fmtDate(result.period_end)}.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              {result.group_by.map((g) => <th key={g} scope="col">{g.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</th>)}
              <th scope="col" className="num">{result.metric_label}</th>
              <th scope="col" className="bar-col"><span className="sr-only">Relative size</span></th>
            </tr>
          </thead>
          <tbody>
            {result.groups.map((g, i) => (
              <tr key={i} className={g.suppressed ? "row-suppressed" : undefined}>
                {result.group_by.map((k) => <td key={k}>{(g.group[k] ?? "").replace(/_/g, " ")}</td>)}
                <td className="num">
                  {g.suppressed ? <span className="suppressed"><EyeOff aria-hidden size={14} />Suppressed<span className="sub">{g.suppression_reason}</span></span> : g.value}
                </td>
                <td className="bar-col">
                  {!g.suppressed && <span className="minibar" style={{ width: `${((g.value ?? 0) / max) * 100}%` }} aria-hidden />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Notice tone="info" title={`${result.suppressed_groups} of ${result.groups.length} groups suppressed (minimum group size ${result.min_group_size})`}>
        <p>{result.notice}</p>
        {result.secondary_suppressions > 0 && (
          <p>{result.secondary_suppressions} larger {result.secondary_suppressions === 1 ? "group was" : "groups were"} also hidden so a small group can't be worked out by subtracting from a total.</p>
        )}
      </Notice>
    </div>
  );
}

export function ReleasedRecords({ release }: { release: ReleaseResult }) {
  if (release.aggregate) return <AggregateTable result={release.aggregate} />;
  const records = release.records ?? [];
  return (
    <div>
      {records.map((rec, i) => (
        <dl key={i} className="record">
          {release.released_fields.filter((f) => f !== "full_medical_history").map((f) => (
            <div key={f}><dt>{fieldLabel(f)}</dt><dd>{rec[f] ?? <span className="muted">Not recorded</span>}</dd></div>
          ))}
        </dl>
      ))}
      {release.withheld_fields.length > 0 && (
        <p className="small muted">Withheld and not sent to your browser: {release.withheld_fields.map(fieldLabel).join(", ")}.</p>
      )}
    </div>
  );
}

export function RequestDetailPage() {
  const { id = "" } = useParams();
  const location = useLocation();
  const created = (location.state as { created?: boolean } | null)?.created;
  const r = useResource<RequestDetail>(`/requests/${encodeURIComponent(id)}`);
  const api = useApi();
  const [release, setRelease] = useState<ReleaseResult | null>(null);
  const [releaseErr, setReleaseErr] = useState<ApiError | null>(null);
  const [releasing, setReleasing] = useState(false);

  if (r.loading && !r.data) return <Loading />;
  if (r.error) return <ErrorState error={r.error} onRetry={r.reload} />;
  if (!r.data) return null;
  const d = r.data;

  const doRelease = async () => {
    setReleasing(true);
    setReleaseErr(null);
    try {
      setRelease(await api<ReleaseResult>(`/requests/${d.id}/release`, { method: "POST" }));
    } catch (e) {
      setReleaseErr(e instanceof ApiError ? e : new ApiError(0, null));
      r.reload();
    } finally {
      setReleasing(false);
    }
  };

  return (
    <>
      <PageHeader title={`${reqNo(d.request_number)}: ${d.purpose_label}`}
        lead={<span className="header-status"><StatusBadge status={d.status} /> Evaluated {fmtDateTime(d.evaluated_at)} under policy {d.policy_version}</span>} />
      {created && (
        <Notice tone={d.status === "rejected" ? "warning" : "success"} title="Request submitted">
          The server evaluated it as {STATUS_LABEL[d.status].toLowerCase()}. The submission and decision are in the audit trail.
        </Notice>
      )}
      <div className="detail-grid">
        <div className="detail-main">
          <section className="panel">
            <h2 className="panel-title">Decision</h2>
            <p>{d.decision_explanation}</p>
            {d.review_reasons.length > 0 && (
              <ul className="plain small">{d.review_reasons.map((x) => <li key={x}>{x}</li>)}</ul>
            )}
            {d.scope_type === "patient" ? (
              <FieldLedger requested={d.requested_fields} decisions={d.field_decisions} pending={d.status === "pending_review"} />
            ) : (
              <p className="small muted">Aggregate request: no patient-level fields are released.</p>
            )}
            <p className="small">Policy codes: <CodeList codes={d.policy_codes} /></p>
          </section>

          {(d.can_release || release || releaseErr) && (
            <section className="panel">
              <h2 className="panel-title">{d.scope_type === "aggregate" ? "Results" : "Released data"}</h2>
              {!release && (
                <>
                  <p className="small">Opening the data re-checks authorisation now: identity, consent, references and the active policy.
                    Only fields that still pass are sent.</p>
                  <button type="button" className="btn btn-primary" onClick={() => void doRelease()} disabled={releasing}>
                    <Eye aria-hidden size={16} /> {releasing ? "Re-checking and opening…" : d.scope_type === "aggregate" ? "Run and view results" : "Open released data"}
                  </button>
                </>
              )}
              {releaseErr && <ApiErrorNotice error={releaseErr} />}
              {release && (
                <>
                  <Notice tone="success" title={`Released ${fmtDateTime(release.released_at)}`}>
                    Re-evaluated under policy {release.policy_version}. This release is recorded in the audit trail.
                  </Notice>
                  <ReleasedRecords release={release} />
                </>
              )}
            </section>
          )}

          {d.can_review && <ReviewForm request={d} onDone={r.reload} />}

          <section className="panel">
            <h2 className="panel-title">Review history</h2>
            {d.reviews.length === 0 ? <p className="muted small">{d.status === "pending_review" ? "Awaiting a privacy reviewer." : "No review was required."}</p> : (
              <ul className="timeline">
                {d.reviews.map((rv) => (
                  <li key={rv.id}>
                    <p><strong>{rv.decision === "approve" ? "Approved" : "Rejected"}</strong> by {rv.reviewer_name}{rv.reviewer_org ? `, ${rv.reviewer_org}` : ""}
                      <span className="muted small"> {fmtDateTime(rv.created_at)}</span></p>
                    <blockquote>{rv.justification}</blockquote>
                    <p className="small muted">Resulting decision: {STATUS_LABEL[rv.resulting_status]}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h2 className="panel-title">Audit events for this request</h2>
            <EventList events={d.audit_events} empty="No events." compact />
          </section>
        </div>

        <aside className="detail-side">
          <section className="panel">
            <h2 className="panel-title">Request</h2>
            <dl className="kv">
              <dt>Requested by</dt><dd>{d.requester_name}<div className="sub">{d.requester_org.name}</div></dd>
              {d.custodian_org && <><dt>Record held by</dt><dd>{d.custodian_org.name}</dd></>}
              <dt>Subject</dt>
              <dd>{d.scope_type === "aggregate" ? "Aggregate statistics" : d.patient_reference ? <>{d.patient_reference}<div className="sub">{d.patient_alias}</div></> : <span className="muted">Redacted</span>}</dd>
              <dt>Authorisation</dt><dd>{d.authorization_reference ?? "None"}</dd>
              {d.scope_type === "patient" && <><dt>Consent record</dt><dd>{d.consent_reference ?? <span className="muted">None found</span>}</dd></>}
              {d.encounter_sensitive !== null && <><dt>Sensitive category</dt><dd>{d.encounter_sensitive ? "Yes" : "No"}</dd></>}
              {d.aggregate_query && (
                <>
                  <dt>Query</dt>
                  <dd>{d.aggregate_query.metric.replace(/_/g, " ")} by {d.aggregate_query.group_by.join(", ").replace(/_/g, " ")}
                    <div className="sub">{fmtDate(d.aggregate_query.period_start)} to {fmtDate(d.aggregate_query.period_end)}</div></dd>
                </>
              )}
              <dt>Submitted</dt><dd>{fmtDateTime(d.created_at)}</dd>
              <dt>Releases</dt><dd>{d.releases.length === 0 ? "None" : d.releases.map((x) => fmtDateTime(x.released_at)).join("; ")}</dd>
            </dl>
          </section>
          <section className="panel">
            <h2 className="panel-title">Justification</h2>
            <p className="small">{d.justification}</p>
          </section>
          <p className="small"><Link to="/requests">Back to requests</Link></p>
        </aside>
      </div>
    </>
  );
}

function ReviewForm({ request, onDone }: { request: RequestDetail; onDone: () => void }) {
  const api = useApi();
  const [decision, setDecision] = useState<"approve" | "reject" | "">("");
  const [justification, setJustification] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const valid = decision !== "" && justification.trim().length >= 20;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/reviews/${request.id}`, { method: "POST", body: { decision, justification: justification.trim() } });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, null));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel panel-accent" aria-labelledby="review-heading">
      <h2 className="panel-title" id="review-heading">Your review</h2>
      <p className="small">
        Approving releases only the fields marked awaiting review. Fields the policy never shares for this purpose stay
        withheld, and the request is re-evaluated against current conditions when you decide.
      </p>
      <form onSubmit={(e) => void submit(e)} noValidate>
        <fieldset className="choice">
          <legend>Decision</legend>
          <label><input type="radio" name="decision" value="approve" checked={decision === "approve"} onChange={() => setDecision("approve")} /> Approve the review fields</label>
          <label><input type="radio" name="decision" value="reject" checked={decision === "reject"} onChange={() => setDecision("reject")} /> Reject the request</label>
        </fieldset>
        <label className="field">
          <span>Justification</span>
          <textarea rows={3} maxLength={2000} value={justification} onChange={(e) => setJustification(e.target.value)}
            aria-invalid={touched && justification.trim().length < 20} />
          <small>At least 20 characters. Stored with your name and shown to the requester.</small>
        </label>
        {touched && !valid && <Notice tone="warning">Choose a decision and give a justification of at least 20 characters.</Notice>}
        {error && <ApiErrorNotice error={error} />}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Recording…" : "Record decision"}</button>
      </form>
    </section>
  );
}
