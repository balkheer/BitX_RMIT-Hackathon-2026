import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, useApi, useResource } from "../api/client";
import type { Mandate, Policy, ReleaseResult, RequestDetail, RequestSummary } from "../api/types";
import { useMe } from "../auth/session";
import { ErrorState, Loading, Notice, PageHeader, StatusBadge } from "../components/ui";
import { reqNo } from "../labels";
import { ApiErrorNotice } from "./NewRequest";
import { RequestTable } from "./Requests";
import { AggregateTable } from "./RequestDetail";

const PERIODS: { label: string; start: string; end: string }[] = [
  { label: "Jan 2025 to Sep 2026 (full synthetic range)", start: "2025-01-01", end: "2026-09-30" },
  { label: "Calendar year 2025", start: "2025-01-01", end: "2025-12-31" },
  { label: "2026 Q1", start: "2026-01-01", end: "2026-03-31" },
  { label: "2026 Q2", start: "2026-04-01", end: "2026-06-30" },
  { label: "2026 Q3", start: "2026-07-01", end: "2026-09-30" },
];

export function StatisticsPage() {
  const me = useMe();
  const api = useApi();
  const policy = useResource<Policy>("/policy");
  const mandates = useResource<Mandate[]>("/mandates");
  const past = useResource<RequestSummary[]>("/requests?purpose=public_health_reporting");
  const [mandate, setMandate] = useState("");
  const [metric, setMetric] = useState("encounter_count");
  const [groupBy, setGroupBy] = useState("diagnosis_category");
  const [period, setPeriod] = useState(0);
  const [justification, setJustification] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<{ request: RequestDetail; release: ReleaseResult | null } | null>(null);

  if (me.role !== "government_officer") {
    return <ErrorState error={new ApiError(403, { error: { code: "FORBIDDEN", message: "Aggregate reporting is available to government health officers." } })} />;
  }
  if (policy.loading || mandates.loading) return <Loading />;
  if (policy.error || !policy.data) return <ErrorState error={policy.error ?? new Error("Policy unavailable")} />;
  const agg = policy.data.document.purposes.public_health_reporting?.aggregate;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setOutcome(null);
    const p = PERIODS[period]!;
    if (!mandate || justification.trim().length < 10) {
      setError(new ApiError(422, { error: { code: "VALIDATION_FAILED", message: "Choose a mandate and give a reason of at least 10 characters." } }));
      return;
    }
    setBusy(true);
    try {
      const created = await api<{ request: RequestDetail }>("/requests", {
        method: "POST",
        body: { purpose: "public_health_reporting", mandate_reference: mandate, justification: justification.trim(),
          aggregate: { metric, group_by: [groupBy], period_start: p.start, period_end: p.end } },
      });
      let release: ReleaseResult | null = null;
      if (created.request.can_release) {
        release = await api<ReleaseResult>(`/requests/${created.request.id}/release`, { method: "POST" });
      }
      setOutcome({ request: created.request, release });
      past.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, null));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Public-health statistics"
        lead="Aggregate counts only. No patient-level records leave the custodians, and small groups are suppressed." />
      <div className="form-grid">
        <form className="panel form-main" onSubmit={(e) => void submit(e)} noValidate>
          <h2 className="panel-title">New aggregate query</h2>
          <label className="field">
            <span>Reporting mandate</span>
            <select value={mandate} onChange={(e) => setMandate(e.target.value)}>
              <option value="">Choose your agency's mandate</option>
              {mandates.data?.map((m) => (
                <option key={m.mandate_reference} value={m.mandate_reference}>
                  {m.mandate_reference}: {m.description}{m.is_active ? "" : " (inactive)"}
                </option>
              ))}
            </select>
          </label>
          <div className="row">
            <label className="field">
              <span>Measure</span>
              <select value={metric} onChange={(e) => setMetric(e.target.value)}>
                {agg?.metrics.map((m) => <option key={m} value={m}>{policy.data!.metrics[m]}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Group by</span>
              <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
                {agg?.group_by.map((g) => <option key={g} value={g}>{policy.data!.groupings[g]}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Reporting period</span>
              <select value={period} onChange={(e) => setPeriod(Number(e.target.value))}>
                {PERIODS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
              </select>
            </label>
          </div>
          <label className="field">
            <span>Reason</span>
            <textarea rows={2} maxLength={2000} value={justification} onChange={(e) => setJustification(e.target.value)}
              placeholder="For example: quarterly chronic disease surveillance report." />
          </label>
          {error && <ApiErrorNotice error={error} />}
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Evaluating and computing…" : "Run query"}</button>
        </form>
        <aside className="form-side panel">
          <h2 className="panel-title">Reporting rules</h2>
          <ul className="plain small">
            <li>Groups smaller than {policy.data.effective_min_group_size} are suppressed.</li>
            <li>One grouping dimension per query, to limit cross-tabulation.</li>
            <li>An active mandate held by your agency is required.</li>
            <li>Suppression lowers re-identification risk but cannot remove it. Overlapping queries are recorded and reviewable.</li>
          </ul>
        </aside>
      </div>
      {outcome && (
        <section className="panel section" aria-live="polite">
          <div className="section-head">
            <h2>Result for <Link to={`/requests/${outcome.request.id}`}>{reqNo(outcome.request.request_number)}</Link></h2>
            <StatusBadge status={outcome.request.status} />
          </div>
          {outcome.release?.aggregate ? <AggregateTable result={outcome.release.aggregate} /> : (
            <Notice tone="danger" title="No results released">{outcome.request.decision_explanation}</Notice>
          )}
        </section>
      )}
      <section className="section">
        <h2>Previous queries</h2>
        {past.data && past.data.length > 0 ? <RequestTable rows={past.data} showRequester={false} /> : <p className="muted">None yet.</p>}
      </section>
    </>
  );
}
