import { ShieldCheck, ShieldX } from "lucide-react";
import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, useApi, useResource } from "../api/client";
import type { AuditEvent, AuditPage as AuditPageT, VerifyResult } from "../api/types";
import { can, useMe } from "../auth/session";
import { CodeList, Empty, ErrorState, Loading, Notice, PageHeader } from "../components/ui";
import { ACTION_LABEL, actionLabel, fmtDateTime, reqNo } from "../labels";

const OUTCOME_LABEL: Record<AuditEvent["outcome"], string> = { success: "Success", denied: "Denied", failure: "Failure", info: "Info" };

export function EventList({ events, empty, compact }: { events: AuditEvent[]; empty: string; compact?: boolean }) {
  if (!events.length) return <p className="muted">{empty}</p>;
  return (
    <ol className={`events ${compact ? "events-compact" : ""}`}>
      {events.map((e) => (
        <li key={e.seq} className={`event event-${e.outcome}`}>
          <span className="event-mark" aria-hidden />
          <div>
            <p className="event-line">
              <strong>{actionLabel(e.action)}</strong>
              {e.request_number && e.request_id && <> on <Link to={`/requests/${e.request_id}`}>{reqNo(e.request_number)}</Link></>}
              {e.decision && <span className="muted"> ({e.decision.replace(/_/g, " ")})</span>}
            </p>
            <p className="sub">
              {e.actor_display ?? "System"}{e.actor_org_name ? `, ${e.actor_org_name}` : ""}. {fmtDateTime(e.occurred_at)}. Event {e.seq}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function AuditPage() {
  const me = useMe();
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const offset = Number(params.get("offset") ?? 0) || 0;
  const action = params.get("action") ?? "";
  const outcome = params.get("outcome") ?? "";
  const qs = new URLSearchParams({ limit: "50", offset: String(offset) });
  if (action) qs.set("action", action);
  if (outcome) qs.set("outcome", outcome);
  const page = useResource<AuditPageT>(`/audit?${qs.toString()}`);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [verifyErr, setVerifyErr] = useState<ApiError | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(params);
    if (v) n.set(k, v); else n.delete(k);
    n.delete("offset");
    setParams(n, { replace: true });
  };

  const runVerify = async () => {
    setVerifying(true);
    setVerifyErr(null);
    try {
      setVerify(await api<VerifyResult>("/audit/verify", { method: "POST" }));
      page.reload();
    } catch (e) {
      setVerifyErr(e instanceof ApiError ? e : new ApiError(0, null));
    } finally {
      setVerifying(false);
    }
  };

  return (
    <>
      <PageHeader title="Audit trail" lead={page.data?.scope_description}
        actions={can(me, "audit:verify") ? (
          <button type="button" className="btn btn-primary" onClick={() => void runVerify()} disabled={verifying}>
            <ShieldCheck aria-hidden size={16} /> {verifying ? "Verifying chain…" : "Verify integrity"}
          </button>
        ) : undefined} />

      {verify && (
        <div className={`verify ${verify.valid ? "verify-ok" : "verify-bad"}`} role="status" aria-live="polite">
          {verify.valid ? <ShieldCheck aria-hidden size={28} /> : <ShieldX aria-hidden size={28} />}
          <div>
            <p className="verify-title">
              {verify.valid ? `Chain intact: all ${verify.checked_events} events verified` : `Integrity check failed at event ${verify.first_invalid_seq}`}
            </p>
            {verify.problem && <p>{verify.problem}</p>}
            {verify.head_hash && <p className="small">Head event {verify.head_seq}, hash <code className="hash">{verify.head_hash}</code></p>}
            <p className="small muted">{verify.notice} Checked {fmtDateTime(verify.verified_at)}.</p>
          </div>
        </div>
      )}
      {verifyErr && <ErrorState error={verifyErr} />}

      <div className="filters">
        <label className="field">
          <span>Action</span>
          <select value={action} onChange={(e) => set("action", e.target.value)}>
            <option value="">All actions</option>
            {Object.entries(ACTION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Outcome</span>
          <select value={outcome} onChange={(e) => set("outcome", e.target.value)}>
            <option value="">All outcomes</option>
            {Object.entries(OUTCOME_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>

      {page.loading && <Loading />}
      {page.error && <ErrorState error={page.error} onRetry={page.reload} />}
      {page.data && page.data.items.length === 0 && <Empty title="No audit events match" />}
      {page.data && page.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table audit-table">
              <thead>
                <tr>
                  <th scope="col">Event</th><th scope="col">Time</th><th scope="col">Actor</th><th scope="col">Action</th>
                  <th scope="col">Request</th><th scope="col">Decision</th><th scope="col">Policy codes</th><th scope="col">Hash</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((e) => (
                  <Fragment key={e.seq}>
                    <tr className={`outcome-${e.outcome}`}>
                      <td className="num">{e.seq}</td>
                      <td className="nowrap">{fmtDateTime(e.occurred_at)}</td>
                      <td>{e.actor_display ?? "System"}<div className="sub">{e.actor_org_name}</div></td>
                      <td>
                        <button type="button" className="linklike" aria-expanded={open === e.seq} onClick={() => setOpen(open === e.seq ? null : e.seq)}>
                          {actionLabel(e.action)}
                        </button>
                        <div className="sub">{OUTCOME_LABEL[e.outcome]}</div>
                      </td>
                      <td>{e.request_id && e.request_number ? <Link to={`/requests/${e.request_id}`}>{reqNo(e.request_number)}</Link> : <span className="muted">None</span>}</td>
                      <td>{e.decision?.replace(/_/g, " ") ?? <span className="muted">None</span>}</td>
                      <td><CodeList codes={e.policy_codes} /></td>
                      <td>
                        <span className={e.hash_valid ? "hash-ok" : "hash-bad"} title={e.event_hash}>
                          {e.hash_valid ? "Matches" : "Mismatch"} <code>{e.event_hash.slice(0, 10)}</code>
                        </span>
                      </td>
                    </tr>
                    {open === e.seq && (
                      <tr className="event-details">
                        <td colSpan={8}>
                          <p className="small muted">Details contain identifiers and field names only, never patient values or credentials.</p>
                          <pre>{JSON.stringify(e.details, null, 2)}</pre>
                          <p className="small">Previous hash <code className="hash">{e.prev_hash}</code></p>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="small muted">
              Showing {offset + 1} to {offset + page.data.items.length} of {page.data.total}
            </span>
            <button type="button" className="btn btn-secondary" disabled={offset === 0} onClick={() => set2(params, setParams, Math.max(0, offset - 50))}>Newer</button>
            <button type="button" className="btn btn-secondary" disabled={offset + 50 >= page.data.total} onClick={() => set2(params, setParams, offset + 50)}>Older</button>
          </div>
          {!verify && can(me, "audit:verify") && (
            <Notice tone="info">The Hash column re-checks each displayed event's contents. Use Verify integrity to check every link in the full chain.</Notice>
          )}
        </>
      )}
    </>
  );
}

function set2(params: URLSearchParams, setParams: (p: URLSearchParams, o: { replace: boolean }) => void, offset: number) {
  const n = new URLSearchParams(params);
  n.set("offset", String(offset));
  setParams(n, { replace: true });
}
