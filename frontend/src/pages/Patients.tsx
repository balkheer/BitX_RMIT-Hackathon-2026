import { Lock } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useResource } from "../api/client";
import type { PatientDetail, PatientSummary } from "../api/types";
import { useMe } from "../auth/session";
import { Empty, ErrorState, Loading, PageHeader } from "../components/ui";
import { fieldLabel, fmtDate } from "../labels";

export function PatientsPage() {
  const me = useMe();
  const rows = useResource<PatientSummary[]>("/patients");
  return (
    <>
      <PageHeader title="Patient records" lead={`Synthetic patients whose records ${me.organization.name} holds. Records held by other organisations are reached only through a data-sharing request.`} />
      {rows.loading && <Loading />}
      {rows.error && <ErrorState error={rows.error} onRetry={rows.reload} />}
      {rows.data && rows.data.length === 0 && <Empty title="Your organisation holds no patient records" />}
      {rows.data && rows.data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Reference</th><th scope="col">Name (synthetic)</th><th scope="col">Born</th><th scope="col">Sex</th><th scope="col">Region</th><th scope="col" className="num">Encounters</th></tr></thead>
            <tbody>
              {rows.data.map((p) => (
                <tr key={p.id}>
                  <td><Link className="strong-link" to={`/patients/${p.id}`}>{p.synthetic_ref}</Link></td>
                  <td>{p.display_alias}</td><td>{p.birth_year}</td><td>{p.sex}</td><td>{p.region}</td>
                  <td className="num">{p.encounter_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function PatientDetailPage() {
  const { id = "" } = useParams();
  const p = useResource<PatientDetail>(`/patients/${encodeURIComponent(id)}`);
  if (p.loading) return <Loading />;
  if (p.error) return <ErrorState error={p.error} onRetry={p.reload} />;
  if (!p.data) return null;
  const d = p.data;
  return (
    <>
      <PageHeader title={`${d.synthetic_ref}: ${d.display_alias}`} lead={`Born ${d.birth_year}, ${d.sex}, ${d.region}. Held by ${d.custodian_org}. This view is recorded in the audit trail.`} />
      <section className="section">
        <h2>Encounters</h2>
        {d.encounters.length === 0 ? <p className="muted">No encounters.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Date</th><th scope="col">Diagnosis</th><th scope="col">Procedure</th><th scope="col">Medication</th><th scope="col">Clinician notes</th><th scope="col">Sensitive notes</th><th scope="col" className="num">Billed</th></tr></thead>
              <tbody>
                {d.encounters.map((e) => (
                  <tr key={e.encounter_reference}>
                    <td className="nowrap">{fmtDate(e.treatment_date)}<div className="sub">{e.encounter_reference}</div></td>
                    <td>{e.diagnosis_code} {e.diagnosis_description}<div className="sub">{e.diagnosis_category.replace(/_/g, " ")}{e.sensitive_category ? ", sensitive category" : ""}</div></td>
                    <td>{e.procedure_code}</td><td>{e.medication}</td><td className="small">{e.clinician_notes}</td>
                    <td>{e.has_sensitive_notes ? <span className="restricted"><Lock aria-hidden size={14} /> Restricted</span> : <span className="muted">None</span>}</td>
                    <td className="num">${e.billing_amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small muted">Sensitive notes are never sent to this view. They can only leave the custodian through a reviewed continuity-of-care request.</p>
      </section>
      <section className="section">
        <h2>Consent records</h2>
        {d.consents.length === 0 ? <p className="muted">No consent decisions recorded. Purposes that rely on another legal basis can still apply.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Reference</th><th scope="col">Purpose</th><th scope="col">Organisation</th><th scope="col">Status</th><th scope="col">Excluded fields</th><th scope="col">Valid until</th></tr></thead>
              <tbody>
                {d.consents.map((c) => (
                  <tr key={c.consent_reference}>
                    <td>{c.consent_reference}</td><td>{c.purpose.replace(/_/g, " ")}</td><td>{c.grantee_org ?? "Any eligible organisation"}</td>
                    <td><span className={`consent consent-${c.status}`}>{c.status}</span></td>
                    <td>{c.excluded_fields.length ? c.excluded_fields.map(fieldLabel).join(", ") : <span className="muted">None</span>}</td>
                    <td>{c.valid_until ? fmtDate(c.valid_until) : "Open"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="small"><Link to="/patients">Back to patient records</Link></p>
    </>
  );
}
