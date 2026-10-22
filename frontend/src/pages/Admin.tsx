import { useState, type FormEvent } from "react";
import { ApiError, useApi, useResource } from "../api/client";
import type { AdminOrg, AdminUser, Policy } from "../api/types";
import { can, useMe } from "../auth/session";
import { ErrorState, Loading, Notice, PageHeader } from "../components/ui";
import { ROLE_LABEL, fmtDateTime } from "../labels";
import { ApiErrorNotice } from "./NewRequest";

export function AdminPage() {
  const me = useMe();
  if (!can(me, "admin:users")) {
    return <ErrorState error={new ApiError(403, { error: { code: "FORBIDDEN", message: "Administration is limited to platform administrators." } })} />;
  }
  return (
    <>
      <PageHeader title="Administration"
        lead="Manage who can use ConsentBridge and the active policy. Administrators can't read patient data, release data or decide reviews, and every change here is audited." />
      <UsersSection />
      <OrgsSection />
      <PolicyEditor />
    </>
  );
}

function UsersSection() {
  const api = useApi();
  const users = useResource<AdminUser[]>("/admin/users");
  const orgs = useResource<AdminOrg[]>("/admin/organizations");
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [form, setForm] = useState({ username: "", display_name: "", organization_slug: "", role: "hospital_staff", external_subject: "" });

  const patch = async (u: AdminUser, body: Record<string, unknown>, done: string) => {
    setErr(null);
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", body });
      setMsg({ tone: "success", text: done });
      users.reload();
    } catch (e) { setErr(e as ApiError); }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await api("/admin/users", { method: "POST", body: { ...form, external_subject: form.external_subject || null } });
      setMsg({ tone: "success", text: `User ${form.username} created.` });
      setForm({ username: "", display_name: "", organization_slug: "", role: "hospital_staff", external_subject: "" });
      users.reload();
    } catch (e2) { setErr(e2 as ApiError); }
  };

  return (
    <section className="section" aria-labelledby="users-h">
      <h2 id="users-h">Users and identity links</h2>
      <p className="small muted">A user can sign in only after their TideCloak subject is linked here or with the operator CLI. Roles come from this table, never from the token.</p>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {err && <ApiErrorNotice error={err} />}
      {users.loading && <Loading />}
      {users.error && <ErrorState error={users.error} />}
      {users.data && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">User</th><th scope="col">Role</th><th scope="col">Organisation</th><th scope="col">Identity</th><th scope="col">Last seen</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {users.data.map((u) => (
                <tr key={u.id}>
                  <td>{u.display_name}<div className="sub">{u.username}</div></td>
                  <td>{ROLE_LABEL[u.role]}</td>
                  <td>{u.organization.name}</td>
                  <td>{u.identity_linked ? "Linked" : <span className="muted">Not linked</span>}</td>
                  <td className="nowrap">{u.last_seen_at ? fmtDateTime(u.last_seen_at) : <span className="muted">Never</span>}</td>
                  <td>{u.is_active ? "Active" : <span className="t-withheld">Disabled</span>}</td>
                  <td className="actions">
                    {u.role !== "platform_admin" && (
                      <>
                        <button type="button" className="btn btn-small btn-secondary"
                          onClick={() => void patch(u, { is_active: !u.is_active }, `${u.username} ${u.is_active ? "disabled" : "enabled"}.`)}>
                          {u.is_active ? "Disable" : "Enable"}
                        </button>
                        <LinkIdentity onLink={(sub) => void patch(u, { external_subject: sub }, `${u.username} linked to a TideCloak identity.`)} />
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form className="panel inline-form" onSubmit={(e) => void create(e)}>
        <h3>Add a user</h3>
        <div className="row">
          <label className="field"><span>Username</span><input required pattern="[a-z0-9._-]+" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
          <label className="field"><span>Display name</span><input required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></label>
          <label className="field"><span>Organisation</span>
            <select required value={form.organization_slug} onChange={(e) => setForm({ ...form, organization_slug: e.target.value })}>
              <option value="">Choose</option>
              {orgs.data?.filter((o) => o.type !== "platform").map((o) => <option key={o.slug} value={o.slug}>{o.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Role</span>
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {(["hospital_staff", "privacy_reviewer", "insurance_officer", "government_officer"] as const).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
          </label>
          <label className="field grow"><span>TideCloak subject (optional)</span><input value={form.external_subject} onChange={(e) => setForm({ ...form, external_subject: e.target.value })} placeholder="The user's sub claim" /></label>
        </div>
        <button type="submit" className="btn btn-primary">Add user</button>
      </form>
    </section>
  );
}

function LinkIdentity({ onLink }: { onLink: (sub: string) => void }) {
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState("");
  if (!open) return <button type="button" className="btn btn-small btn-ghost" onClick={() => setOpen(true)}>Link identity</button>;
  return (
    <span className="link-identity">
      <input aria-label="TideCloak subject" value={sub} onChange={(e) => setSub(e.target.value)} placeholder="sub claim" />
      <button type="button" className="btn btn-small btn-primary" disabled={!sub.trim()} onClick={() => { onLink(sub.trim()); setOpen(false); setSub(""); }}>Save</button>
    </span>
  );
}

function OrgsSection() {
  const api = useApi();
  const orgs = useResource<AdminOrg[]>("/admin/organizations");
  const [err, setErr] = useState<ApiError | null>(null);
  const toggle = async (o: AdminOrg) => {
    setErr(null);
    try { await api(`/admin/organizations/${o.id}`, { method: "PATCH", body: { is_active: !o.is_active } }); orgs.reload(); }
    catch (e) { setErr(e as ApiError); }
  };
  return (
    <section className="section" aria-labelledby="orgs-h">
      <h2 id="orgs-h">Organisations</h2>
      {err && <ApiErrorNotice error={err} />}
      {orgs.data && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Organisation</th><th scope="col">Type</th><th scope="col" className="num">Users</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {orgs.data.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}<div className="sub">{o.slug}</div></td><td>{o.type}</td><td className="num">{o.user_count}</td>
                  <td>{o.is_active ? "Active" : <span className="t-withheld">Disabled</span>}</td>
                  <td className="actions">{o.type !== "platform" && (
                    <button type="button" className="btn btn-small btn-secondary" onClick={() => void toggle(o)}>{o.is_active ? "Disable" : "Enable"}</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function bump(v: string): string {
  const [y, m, n] = v.split(".");
  return `${y}.${m}.${Number(n) + 1}`;
}

function PolicyEditor() {
  const api = useApi();
  const policy = useResource<Policy>("/policy");
  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!policy.data) return policy.loading ? <Loading /> : null;
  const current = text ?? JSON.stringify({ ...policy.data.document, version: bump(policy.data.version) }, null, 2);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setOk(null);
    let doc: unknown;
    try { doc = JSON.parse(current); } catch {
      setErr(new ApiError(422, { error: { code: "INVALID_JSON", message: "The policy document is not valid JSON." } }));
      return;
    }
    setBusy(true);
    try {
      const res = await api<Policy>("/admin/policy", { method: "POST", body: { document: doc, change_note: note } });
      setOk(`Policy ${res.version} is now active. New evaluations and every future release use it.`);
      setText(null);
      setNote("");
      policy.reload();
    } catch (e2) { setErr(e2 as ApiError); } finally { setBusy(false); }
  };

  return (
    <section className="section" aria-labelledby="pol-h">
      <h2 id="pol-h">Publish a new policy version</h2>
      <p className="small muted">
        The server validates the document: only catalogue fields, no field in two buckets, sensitive notes never freely allowed,
        administrators and reviewers never granted a request purpose, and a minimum group size of at least 5.
        The version must increase. Requests already released are unaffected; pending releases are re-evaluated against the new version.
      </p>
      <form onSubmit={(e) => void submit(e)} className="panel">
        <label className="field">
          <span>Policy document (JSON)</span>
          <textarea className="code-area" rows={16} spellCheck={false} value={current} onChange={(e) => setText(e.target.value)} />
        </label>
        <label className="field">
          <span>Change note</span>
          <input value={note} minLength={10} maxLength={1000} required onChange={(e) => setNote(e.target.value)} placeholder="What changed and why" />
        </label>
        {err && <ApiErrorNotice error={err} />}
        {ok && <Notice tone="success">{ok}</Notice>}
        <button type="submit" className="btn btn-primary" disabled={busy || note.trim().length < 10}>{busy ? "Validating…" : "Validate and activate"}</button>
      </form>
    </section>
  );
}
