import { Link } from "react-router-dom";
import { useResource } from "../api/client";
import type { Dashboard, RequestStatus } from "../api/types";
import { can, useMe } from "../auth/session";
import { ErrorState, Loading, PageHeader } from "../components/ui";
import { STATUS_LABEL } from "../labels";
import { EventList } from "./Audit";

const ORDER: RequestStatus[] = ["approved", "approved_with_restrictions", "pending_review", "rejected"];

export function StatusBar({ d }: { d: Dashboard }) {
  const counts: Record<RequestStatus, number> = {
    approved: d.approved, approved_with_restrictions: d.approved_with_restrictions,
    pending_review: d.pending_review, rejected: d.rejected,
  };
  const total = d.total_requests || 1;
  return (
    <section className="statusbar-wrap" aria-labelledby="status-heading">
      <div className="statusbar-head">
        <h2 id="status-heading">Decisions on visible requests</h2>
        <p className="total"><strong>{d.total_requests}</strong> requests</p>
      </div>
      <div className="statusbar" role="img"
        aria-label={ORDER.map((s) => `${STATUS_LABEL[s]} ${counts[s]}`).join(", ")}>
        {ORDER.map((s) => counts[s] > 0 && (
          <span key={s} className={`seg seg-${s}`} style={{ flexGrow: counts[s] / total }} />
        ))}
      </div>
      <dl className="statuslegend">
        {ORDER.map((s) => (
          <div key={s}>
            <dt><span className={`dot dot-${s}`} aria-hidden />{STATUS_LABEL[s]}</dt>
            <dd>
              <Link to={`/requests?status=${s}`}>{counts[s]}</Link>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function DashboardPage() {
  const me = useMe();
  const d = useResource<Dashboard>("/dashboard");
  return (
    <>
      <PageHeader
        title="Overview"
        lead={d.data?.scope_description}
        actions={can(me, "requests:create") ? <Link className="btn btn-primary" to="/requests/new">New request</Link> : undefined}
      />
      {d.loading && <Loading />}
      {d.error && <ErrorState error={d.error} onRetry={d.reload} />}
      {d.data && (
        <>
          <StatusBar d={d.data} />
          <div className="facts">
            <p><strong>{d.data.releases}</strong> data releases recorded</p>
            {d.data.review_queue !== null && (
              <p>
                <strong>{d.data.review_queue}</strong> {d.data.review_queue === 1 ? "request is" : "requests are"} waiting
                for your review. <Link to="/reviews">Open the review queue</Link>
              </p>
            )}
            {me.role === "government_officer" && (
              <p>Aggregate reports only. <Link to="/statistics">Run a statistics query</Link></p>
            )}
          </div>
          <section aria-labelledby="recent-heading" className="section">
            <div className="section-head">
              <h2 id="recent-heading">Recent activity</h2>
              <Link to="/audit">Full audit trail</Link>
            </div>
            <EventList events={d.data.recent_events} empty="No activity involving your organisation yet." />
          </section>
        </>
      )}
    </>
  );
}
