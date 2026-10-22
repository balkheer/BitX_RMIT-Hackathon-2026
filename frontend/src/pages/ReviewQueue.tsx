import { Link } from "react-router-dom";
import { useResource } from "../api/client";
import type { RequestSummary } from "../api/types";
import { useMe } from "../auth/session";
import { CodeList, Empty, ErrorState, Loading, PageHeader, StatusBadge } from "../components/ui";
import { fieldLabel, fmtDateTime, reqNo } from "../labels";
import { RequestTable } from "./Requests";

export function ReviewQueuePage() {
  const me = useMe();
  const queue = useResource<RequestSummary[]>("/reviews/queue");
  const history = useResource<RequestSummary[]>("/reviews/history");
  return (
    <>
      <PageHeader title="Privacy review queue"
        lead={`Requests for records held by ${me.organization.name} that need a human decision. You can't review requests made by your own organisation.`} />
      {queue.loading && <Loading />}
      {queue.error && <ErrorState error={queue.error} onRetry={queue.reload} />}
      {queue.data && queue.data.length === 0 && <Empty title="Nothing is waiting for review">New review-required requests will appear here.</Empty>}
      {queue.data && queue.data.length > 0 && (
        <ul className="queue">
          {queue.data.map((r) => (
            <li key={r.id} className="queue-item">
              <div className="queue-head">
                <Link to={`/requests/${r.id}`} className="strong-link">{reqNo(r.request_number)}: {r.purpose_label}</Link>
                <StatusBadge status={r.status} />
              </div>
              <dl className="kv kv-inline">
                <dt>From</dt><dd>{r.requester_org.name} ({r.requester_name})</dd>
                <dt>Patient</dt><dd>{r.patient_reference}</dd>
                <dt>Basis</dt><dd>{r.authorization_reference}</dd>
                <dt>Submitted</dt><dd>{fmtDateTime(r.created_at)}</dd>
              </dl>
              <p className="small"><strong>Needs review:</strong> {r.review_fields.map(fieldLabel).join(", ")}</p>
              {r.denied_fields.length > 0 && <p className="small"><strong>Already withheld:</strong> {r.denied_fields.map(fieldLabel).join(", ")}</p>}
              <p className="small">Policy flags: <CodeList codes={r.policy_codes} /></p>
              <Link className="btn btn-primary" to={`/requests/${r.id}`}>Review {reqNo(r.request_number)}</Link>
            </li>
          ))}
        </ul>
      )}
      <section className="section" aria-labelledby="hist">
        <h2 id="hist">Decided reviews</h2>
        {history.loading && <Loading />}
        {history.error && <ErrorState error={history.error} />}
        {history.data && history.data.length === 0 && <p className="muted">No review decisions yet.</p>}
        {history.data && history.data.length > 0 && <RequestTable rows={history.data} />}
      </section>
    </>
  );
}
