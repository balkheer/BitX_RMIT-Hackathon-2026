import { useResource } from "../api/client";
import type { Policy, PurposeRules } from "../api/types";
import { ErrorState, Loading, PageHeader } from "../components/ui";
import { ROLE_LABEL, fieldLabel, fmtDateTime } from "../labels";
import type { Role } from "../api/types";

const BASIS: Record<PurposeRules["authorization"], string> = {
  insurance_claim: "A claim lodged with the requesting insurer for this patient",
  referral: "An active referral from the record holder to the requesting hospital",
  reporting_mandate: "An active public-health reporting mandate held by the agency",
};
const CONSENT: Record<PurposeRules["consent"], string> = {
  required: "Required: a current consent for this purpose and organisation must be on record",
  refusal_blocks: "Not required, but a recorded refusal or withdrawal blocks sharing",
  not_applicable: "Not used: aggregate reporting relies on the mandate",
};

type Cell = "allowed" | "review" | "never" | "unneeded";
function cell(p: PurposeRules, f: string): Cell {
  if (p.fields.allowed.includes(f)) return "allowed";
  if (p.fields.review_required.includes(f)) return "review";
  if (p.fields.hard_denied.includes(f) || p.reject_if_requested.includes(f)) return "never";
  return "unneeded";
}
const CELL_TEXT: Record<Cell, string> = { allowed: "Allowed", review: "Review", never: "Never", unneeded: "Not needed" };

export function PolicyPage() {
  const p = useResource<Policy>("/policy");
  if (p.loading) return <Loading />;
  if (p.error || !p.data) return <ErrorState error={p.error ?? new Error("Policy unavailable")} onRetry={p.reload} />;
  const policy = p.data;
  const purposes = Object.entries(policy.document.purposes);
  const patientPurposes = purposes.filter(([, x]) => x.scope === "patient");

  return (
    <>
      <PageHeader title={`Data-sharing policy ${policy.version}`}
        lead={`Active since ${fmtDateTime(policy.activated_at)}. ${policy.change_note}`} />

      <section className="section">
        <h2>Purposes</h2>
        <div className="purpose-list">
          {purposes.map(([key, x]) => (
            <article key={key} className="panel">
              <h3>{x.label}</h3>
              <p className="small">{x.description}</p>
              <dl className="kv">
                <dt>Who can request</dt><dd>{x.allowed_roles.map((r) => ROLE_LABEL[r as Role] ?? r).join(", ")}</dd>
                <dt>Basis</dt><dd>{BASIS[x.authorization]}</dd>
                <dt>Consent</dt><dd>{CONSENT[x.consent]}</dd>
                {x.reject_if_requested.length > 0 && <><dt>Rejected outright if requested</dt><dd>{x.reject_if_requested.map(fieldLabel).join(", ")}</dd></>}
                {x.scope === "patient" && <><dt>Rejected when more than</dt><dd>{x.max_hard_denied_before_reject} never-shared fields are requested</dd></>}
                {x.sensitive_category_review_fields.length > 0 && <><dt>Review for sensitive encounters</dt><dd>{x.sensitive_category_review_fields.map(fieldLabel).join(", ")}</dd></>}
                {x.aggregate && (
                  <>
                    <dt>Measures</dt><dd>{x.aggregate.metrics.map((m) => policy.metrics[m]).join(", ")}</dd>
                    <dt>Groupings</dt><dd>{x.aggregate.group_by.map((g) => policy.groupings[g]).join(", ")}, one at a time</dd>
                    <dt>Minimum group size</dt><dd>{policy.effective_min_group_size}, with complementary suppression</dd>
                    <dt>Longest period</dt><dd>{x.aggregate.max_period_days} days</dd>
                  </>
                )}
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section className="section">
        <h2>Field permissions by purpose</h2>
        <div className="table-wrap">
          <table className="table matrix">
            <thead>
              <tr><th scope="col">Field</th>{patientPurposes.map(([k, x]) => <th key={k} scope="col">{x.label}</th>)}</tr>
            </thead>
            <tbody>
              {policy.fields.map((f) => (
                <tr key={f.id}>
                  <th scope="row">{f.label}<div className="sub">{f.description}</div></th>
                  {patientPurposes.map(([k, x]) => {
                    const c = cell(x, f.id);
                    return <td key={k}><span className={`cell cell-${c}`}>{CELL_TEXT[c]}</span></td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section two-col">
        <div className="panel">
          <h2 className="panel-title">Always rejected</h2>
          <ul className="plain small">
            <li>The requester or their organisation is inactive, or the identity isn't provisioned.</li>
            <li>The role isn't permitted for the purpose, or the organisation type doesn't match.</li>
            <li>The claim, referral or mandate doesn't exist, isn't the requester's, or is no longer active.</li>
            <li>Consent is required and is missing, refused, withdrawn or expired.</li>
            <li>A field marked Never, or too many Never fields, is requested.</li>
            <li>Authorisation can't be established for any reason (the engine fails closed).</li>
          </ul>
          <p className="small muted">A reviewer cannot override any of these.</p>
        </div>
        <div className="panel">
          <h2 className="panel-title">Sent to a privacy reviewer</h2>
          <ul className="plain small">
            <li>Any field marked Review for the purpose.</li>
            <li>Clinical codes for an encounter in a sensitive category, such as mental or sexual health.</li>
            <li>Reviewers must work for the record holder, and can't review their own organisation's requests.</li>
            <li>The request is re-evaluated when the reviewer decides, and again whenever data is opened.</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <h2>Version history</h2>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Version</th><th scope="col">Change</th><th scope="col">By</th><th scope="col">When</th></tr></thead>
            <tbody>
              {policy.history.map((h) => (
                <tr key={h.version}>
                  <td>{h.version}{h.is_active && <span className="pill">Active</span>}</td>
                  <td>{h.change_note}</td><td>{h.created_by ?? "Bootstrap"}</td><td className="nowrap">{fmtDateTime(h.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">The policy encodes organisational rules for this demonstration. It is not legal advice and does not by itself make any data sharing lawful.</p>
      </section>
    </>
  );
}
