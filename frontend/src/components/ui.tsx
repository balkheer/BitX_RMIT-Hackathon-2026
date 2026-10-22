import { AlertTriangle, CircleCheck, CircleDashed, CircleSlash, Clock3, Loader2, Lock, SearchX, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { RequestStatus } from "../api/types";
import { DECISION_LABEL, STATUS_LABEL, fieldLabel } from "../labels";

const STATUS_ICON: Record<RequestStatus, ReactNode> = {
  approved: <CircleCheck aria-hidden size={15} />,
  approved_with_restrictions: <CircleDashed aria-hidden size={15} />,
  pending_review: <Clock3 aria-hidden size={15} />,
  rejected: <CircleSlash aria-hidden size={15} />,
};

export function StatusBadge({ status }: { status: RequestStatus }) {
  return (
    <span className={`badge badge-${status}`}>
      {STATUS_ICON[status]}
      {STATUS_LABEL[status]}
    </span>
  );
}

export function PageHeader({ title, lead, actions }: { title: string; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {lead && <p className="lead">{lead}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <Loader2 className="spin" aria-hidden size={20} />
      <span>{label}…</span>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <SearchX aria-hidden size={22} />
      <div>
        <p className="state-title">{title}</p>
        {children && <div className="state-body">{children}</div>}
      </div>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : 0;
  const forbidden = status === 403;
  return (
    <div className={`state ${forbidden ? "state-forbidden" : "state-error"}`} role="alert">
      {forbidden ? <Lock aria-hidden size={22} /> : <AlertTriangle aria-hidden size={22} />}
      <div>
        <p className="state-title">{forbidden ? "You don't have access to this" : "This couldn't be loaded"}</p>
        <p className="state-body">{error.message}</p>
        {forbidden && (
          <p className="state-body muted">
            Signing in proves who you are. What you can see is decided separately for your organisation and role, and
            this attempt has been recorded in the audit trail.
          </p>
        )}
        {error instanceof ApiError && error.correlationId && (
          <p className="state-body muted small">Reference: {error.correlationId}</p>
        )}
        {onRetry && !forbidden && (
          <button type="button" className="btn btn-secondary" onClick={onRetry}>Try again</button>
        )}
      </div>
    </div>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "success" | "warning" | "danger"; title?: string; children: ReactNode }) {
  const Icon = tone === "danger" ? ShieldAlert : tone === "success" ? CircleCheck : tone === "warning" ? AlertTriangle : CircleDashed;
  return (
    <div className={`notice notice-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <Icon aria-hidden size={18} />
      <div>
        {title && <p className="notice-title">{title}</p>}
        <div>{children}</div>
      </div>
    </div>
  );
}

type Disposition = "released" | "review" | "withheld";

function disposition(decision: string | undefined): Disposition {
  if (!decision) return "withheld";
  if (decision.startsWith("approved")) return "released";
  if (decision === "review") return "review";
  return "withheld";
}

const DISPOSITION_TEXT: Record<Disposition, string> = {
  released: "Releasable",
  review: "Awaiting review",
  withheld: "Withheld",
};

/**
 * The field disclosure ledger: one row per requested field showing what the policy decided.
 * Shows field identifiers and reasons only; it never contains patient values.
 */
export function FieldLedger({ requested, decisions, pending, caption }: {
  requested: string[];
  decisions: Record<string, string>;
  pending?: boolean;
  caption?: string;
}) {
  const counts = { released: 0, review: 0, withheld: 0 };
  requested.forEach((f) => { counts[disposition(decisions[f])] += 1; });
  return (
    <figure className="ledger">
      <figcaption>
        <span>{caption ?? "Field disclosure"}</span>
        <span className="ledger-tally">
          <span className="tally tally-released">{counts.released} releasable</span>
          {counts.review > 0 && <span className="tally tally-review">{counts.review} awaiting review</span>}
          <span className="tally tally-withheld">{counts.withheld} withheld</span>
        </span>
      </figcaption>
      <ul>
        {requested.map((f) => {
          const d = disposition(decisions[f]);
          return (
            <li key={f} className={`ledger-row ledger-${d}`}>
              <span className="ledger-mark" aria-hidden />
              <span className="ledger-field">
                {fieldLabel(f)}
                <code>{f}</code>
              </span>
              <span className="ledger-status">
                <span className="sr-only">{DISPOSITION_TEXT[d]}: </span>
                {pending && d === "released" ? "Held until review" : DECISION_LABEL[decisions[f] ?? "denied"] ?? "Withheld"}
              </span>
            </li>
          );
        })}
      </ul>
    </figure>
  );
}

export function CodeList({ codes }: { codes: string[] }) {
  if (!codes.length) return <span className="muted">None</span>;
  return (
    <span className="codes">
      {codes.map((c) => <code key={c}>{c}</code>)}
    </span>
  );
}
