import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useResource } from "../api/client";
import type { Policy, RequestSummary } from "../api/types";
import { can, useMe } from "../auth/session";
import { Empty, ErrorState, Loading, PageHeader, StatusBadge } from "../components/ui";
import { STATUS_LABEL, fieldLabel, fmtDateTime, reqNo } from "../labels";

export function FieldCounts({ r }: { r: RequestSummary }) {
  return (
    <span className="fieldcounts" title={`Requested: ${r.requested_fields.map(fieldLabel).join(", ") || "aggregate"}`}>
      {r.scope_type === "aggregate" ? "Aggregate only" : (
        <>
          {r.requested_fields.length} requested, <span className="t-released">{r.approved_fields.length} releasable</span>
          {r.review_fields.length > 0 && <>, <span className="t-review">{r.review_fields.length} in review</span></>}
          {r.denied_fields.length > 0 && <>, <span className="t-withheld">{r.denied_fields.length} withheld</span></>}
        </>
      )}
    </span>
  );
}

export function RequestTable({ rows, showRequester = true }: { rows: RequestSummary[]; showRequester?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Request</th>
            <th scope="col">Purpose</th>
            {showRequester && <th scope="col">Requested by</th>}
            <th scope="col">Subject</th>
            <th scope="col">Fields</th>
            <th scope="col">Decision</th>
            <th scope="col">Submitted</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><Link to={`/requests/${r.id}`} className="strong-link">{reqNo(r.request_number)}</Link>
                <div className="sub">{r.authorization_reference}</div></td>
              <td>{r.purpose_label}</td>
              {showRequester && <td>{r.requester_org.name}<div className="sub">{r.requester_name}</div></td>}
              <td>{r.scope_type === "aggregate" ? <span className="muted">Aggregate</span> : r.patient_reference ? (
                <>{r.patient_reference}<div className="sub">{r.patient_alias}</div></>
              ) : <span className="muted">Redacted</span>}</td>
              <td><FieldCounts r={r} /></td>
              <td><StatusBadge status={r.status} />
                <div className="sub explanation" title={r.decision_explanation}>{r.decision_explanation}</div></td>
              <td className="nowrap">{fmtDateTime(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RequestsPage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const purpose = params.get("purpose") ?? "";
  const q = params.get("q") ?? "";
  const [term, setTerm] = useState(q);
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (purpose) qs.set("purpose", purpose);
  if (q) qs.set("q", q);
  const rows = useResource<RequestSummary[]>(`/requests?${qs.toString()}`);
  const policy = useResource<Policy>("/policy");

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };
  const onSearch = (e: FormEvent) => { e.preventDefault(); update("q", term.trim()); };

  return (
    <>
      <PageHeader
        title="Data-sharing requests"
        lead={me.role === "platform_admin" ? "Operational view. Patient identities are redacted for administrators." : undefined}
        actions={can(me, "requests:create") ? <Link className="btn btn-primary" to="/requests/new">New request</Link> : undefined}
      />
      <form className="filters" onSubmit={onSearch} role="search">
        <label className="field grow">
          <span>Search</span>
          <span className="input-icon">
            <Search aria-hidden size={16} />
            <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="CB-1001, claim or referral reference, patient" maxLength={64} />
          </span>
        </label>
        <label className="field">
          <span>Decision</span>
          <select value={status} onChange={(e) => update("status", e.target.value)}>
            <option value="">All decisions</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Purpose</span>
          <select value={purpose} onChange={(e) => update("purpose", e.target.value)}>
            <option value="">All purposes</option>
            {policy.data && Object.entries(policy.data.document.purposes).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
          </select>
        </label>
        <button type="submit" className="btn btn-secondary">Search</button>
      </form>
      {rows.loading && <Loading />}
      {rows.error && <ErrorState error={rows.error} onRetry={rows.reload} />}
      {rows.data && rows.data.length === 0 && (
        <Empty title={status || purpose || q ? "No requests match these filters" : "No requests yet"}>
          {can(me, "requests:create") ? <Link to="/requests/new">Create a data-sharing request</Link> : "Requests your organisation can see will appear here."}
        </Empty>
      )}
      {rows.data && rows.data.length > 0 && <RequestTable rows={rows.data} />}
    </>
  );
}
