/**
 * UI integration tests. The TideCloak hook is replaced with a test double (only here, never in the app build)
 * and `fetch` is stubbed per test. Real routes, pages and components render.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { FieldLedger } from "../components/ui";
import { AggregateTable } from "../pages/RequestDetail";

const auth = {
  isInitializing: false, initError: null as Error | null, authenticated: true, sessionExpired: false, needsReauth: false,
  isLoading: false, login: vi.fn(), logout: vi.fn(), getToken: vi.fn(async () => "test-token"), triggerReauth: vi.fn(),
  clearReauth: vi.fn(), getValueFromToken: vi.fn((k: string) => (k === "sub" ? "f3c1-subject" : null)),
};

vi.mock("@tidecloak/react", () => ({
  useTideCloak: () => auth,
  TideCloakContextProvider: ({ children }: { children: ReactNode }) => children,
}));

type Route = (url: string, init?: RequestInit) => { status?: number; body: unknown } | undefined;
let routes: Route;
const calls: { url: string; init?: RequestInit }[] = [];

beforeEach(() => {
  Object.assign(auth, { isInitializing: false, initError: null, authenticated: true, sessionExpired: false, needsReauth: false });
  auth.triggerReauth.mockClear();
  auth.login.mockClear();
  calls.length = 0;
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = routes(url, init) ?? { status: 404, body: { error: { code: "NOT_FOUND", message: "Not found." } } };
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const insurer = {
  id: "u1", username: "j.walker", display_name: "Jordan Walker", role: "insurance_officer",
  organization: { id: "o1", name: "Meridian Health Insurance (synthetic)", type: "insurer", slug: "meridian-insurance" },
  permissions: ["dashboard:read", "requests:create", "requests:read", "claims:read", "releases:read", "audit:read", "policy:read"],
};
const dashboard = {
  scope_description: "Requests made by Meridian Health Insurance (synthetic)", total_requests: 7, pending_review: 1,
  approved: 3, approved_with_restrictions: 1, rejected: 2, releases: 2, review_queue: null, recent_events: [],
};

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter>);
}

describe("authentication gate", () => {
  it("sends signed-out users to the TideCloak sign-in screen with no other way in", async () => {
    auth.authenticated = false;
    routes = () => undefined;
    renderAt("/requests");
    const button = await screen.findByRole("button", { name: /sign in with tidecloak/i });
    // The only control is TideCloak sign-in: no demo login, account picker or role switcher.
    expect(screen.getAllByRole("button")).toEqual([button]);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    await userEvent.click(button);
    expect(auth.login).toHaveBeenCalledOnce();
    expect(calls).toHaveLength(0); // nothing fetched from the API while signed out
  });

  it("explains a missing identity-provider configuration instead of offering sign-in", async () => {
    auth.authenticated = false;
    auth.initError = new Error("config");
    routes = () => undefined;
    renderAt("/sign-in");
    expect(await screen.findByText(/sign-in is unavailable/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
  });

  it("shows the session-expired message", async () => {
    auth.authenticated = false;
    sessionStorage.setItem("cb.sessionExpired", "true");
    routes = () => undefined;
    renderAt("/sign-in");
    expect(await screen.findByText(/your session ended/i)).toBeInTheDocument();
  });

  it("blocks a verified identity that is not provisioned (403 from /me)", async () => {
    routes = (url) => url === "/api/me" ? { status: 403, body: { error: { code: "IDENTITY_NOT_MAPPED", message: "Your identity is verified but has not been provisioned for ConsentBridge." } } } : undefined;
    renderAt("/");
    expect(await screen.findByText(/don't have access/i)).toBeInTheDocument();
    expect(screen.getByText(/not been provisioned/i)).toBeInTheDocument();
    expect(screen.getByText("f3c1-subject")).toBeInTheDocument(); // so the user can ask an admin to link it
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("asks for re-authentication when the API rejects the token", async () => {
    routes = (url) => url === "/api/me" ? { status: 401, body: { error: { code: "INVALID_TOKEN", message: "expired" } } } : undefined;
    renderAt("/");
    await waitFor(() => expect(auth.triggerReauth).toHaveBeenCalled());
  });

  it("attaches the bearer token and never sends cookies", async () => {
    routes = (url) => url === "/api/me" ? { body: insurer } : url === "/api/dashboard" ? { body: dashboard } : undefined;
    renderAt("/");
    await screen.findByText(/decisions on visible requests/i);
    const me = calls.find((c) => c.url === "/api/me")!;
    expect((me.init?.headers as Record<string, string>).Authorization).toBe("Bearer test-token");
    expect(me.init?.credentials).toBe("omit");
  });
});

describe("dashboard and navigation", () => {
  it("renders server metrics and only the navigation the role is permitted", async () => {
    routes = (url) => url === "/api/me" ? { body: insurer } : url === "/api/dashboard" ? { body: dashboard } : undefined;
    renderAt("/");
    expect(await screen.findByText("Requests made by Meridian Health Insurance (synthetic)")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /approved 3, approved with restrictions 1, pending review 1, rejected 2/i })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: /primary/i });
    expect(within(nav).getByRole("link", { name: /new request/i })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: /administration/i })).toBeNull();
    expect(within(nav).queryByRole("link", { name: /patient records/i })).toBeNull();
    expect(within(nav).queryByRole("link", { name: /review queue/i })).toBeNull();
  });
});

describe("new request form", () => {
  const policy = {
    version: "2026.10.1", activated_at: "2026-10-22T00:00:00Z", change_note: "", effective_min_group_size: 5,
    metrics: {}, groupings: {}, history: [],
    fields: [
      { id: "billing_amount", label: "Billing amount", sensitivity: "financial", description: "" },
      { id: "clinician_notes", label: "Clinician notes", sensitivity: "clinical", description: "" },
    ],
    document: { version: "2026.10.1", purposes: { insurance_claim_assessment: {
      label: "Insurance claim assessment", description: "Assess a claim.", scope: "patient", allowed_roles: ["insurance_officer"],
      authorization: "insurance_claim", consent: "required",
      fields: { allowed: ["billing_amount"], review_required: [], hard_denied: ["clinician_notes"] },
      reject_if_requested: [], max_hard_denied_before_reject: 2, sensitive_category_review_fields: [], aggregate: null } } },
  };
  const claims = [{ claim_reference: "CLM-M-1001", patient_reference: "SYN-P-0001", patient_alias: "Aster Vale", policy_number: "P",
    amount_claimed: "100.00", status: "lodged", lodged_at: "2026-01-01T00:00:00Z", custodian_org: "St Aurelia" }];

  it("validates before sending, and the preview body carries no organisation, role or status", async () => {
    routes = (url) => {
      if (url === "/api/me") return { body: insurer };
      if (url === "/api/policy") return { body: policy };
      if (url === "/api/claims") return { body: claims };
      if (url === "/api/requests/preview") return { body: { consent_status: "granted", authorization_reference: "CLM-M-1001", decision: {
        status: "approved_with_restrictions", approved_fields: ["billing_amount"], denied_fields: ["clinician_notes"], review_fields: [],
        field_decisions: { billing_amount: "approved", clinician_notes: "denied:hard_deny" }, review_required: false, review_reasons: [],
        policy_codes: ["FIELD_HARD_DENIED"], explanation: "Approved with restrictions.", policy_version: "2026.10.1", evaluated_at: "2026-10-22T00:00:00Z" } } };
      return undefined;
    };
    const user = userEvent.setup();
    renderAt("/requests/new");
    await screen.findByRole("heading", { name: /new data-sharing request/i });
    await user.click(screen.getByRole("button", { name: /check against policy/i }));
    expect(await screen.findByText(/choose the claim/i)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("preview"))).toBe(false);

    await user.selectOptions(await screen.findByRole("combobox", { name: /insurance claim/i }), "CLM-M-1001");
    await user.click(screen.getByRole("checkbox", { name: /billing amount/i }));
    await user.click(screen.getByRole("checkbox", { name: /clinician notes/i }));
    await user.type(screen.getByRole("textbox", { name: /why do you need/i }), "Assessing the lodged claim.");
    await user.click(screen.getByRole("button", { name: /check against policy/i }));

    expect(await screen.findByText(/never shared for this purpose/i, { selector: ".ledger-status" })).toBeInTheDocument();
    const body = JSON.parse(String(calls.find((c) => c.url === "/api/requests/preview")!.init!.body));
    expect(body).toEqual({ purpose: "insurance_claim_assessment", claim_reference: "CLM-M-1001",
      requested_fields: ["billing_amount", "clinician_notes"], justification: "Assessing the lodged claim." });
  });
});

describe("components", () => {
  it("field ledger shows dispositions and identifiers only", () => {
    render(<FieldLedger requested={["billing_amount", "medication", "clinician_notes"]}
      decisions={{ billing_amount: "approved", medication: "review", clinician_notes: "denied:hard_deny" }} pending />);
    expect(screen.getByText("1 releasable")).toBeInTheDocument();
    expect(screen.getByText("1 awaiting review")).toBeInTheDocument();
    expect(screen.getByText("1 withheld")).toBeInTheDocument();
    expect(screen.getByText("Held until review")).toBeInTheDocument(); // approved fields wait while review is pending
    expect(screen.getByText("Never shared for this purpose")).toBeInTheDocument();
  });

  it("aggregate table hides suppressed values and explains why", () => {
    render(<AggregateTable result={{ metric: "encounter_count", metric_label: "Number of encounters", group_by: ["region"],
      period_start: "2025-01-01", period_end: "2026-09-30", min_group_size: 5, suppressed_groups: 2, secondary_suppressions: 1,
      notice: "Suppression reduces but does not eliminate re-identification risk.",
      groups: [
        { group: { region: "Inland" }, value: null, suppressed: true, suppression_reason: "Fewer than 5" },
        { group: { region: "Coastal" }, value: null, suppressed: true, suppression_reason: "Complementary suppression" },
        { group: { region: "North" }, value: 13, suppressed: false, suppression_reason: null },
      ] }} />);
    expect(screen.getAllByText("Suppressed")).toHaveLength(2);
    expect(screen.getByText("Fewer than 5")).toBeInTheDocument();
    expect(screen.getByText("13")).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 groups suppressed/i)).toBeInTheDocument();
    expect(screen.getByText(/also hidden/i)).toBeInTheDocument();
  });
});

describe("audit trail", () => {
  it("runs a backend integrity verification and reports the result", async () => {
    const reviewer = { ...insurer, role: "privacy_reviewer", permissions: ["audit:read", "audit:verify", "policy:read", "reviews:decide"] };
    routes = (url) => {
      if (url === "/api/me") return { body: reviewer };
      if (url.startsWith("/api/audit?")) return { body: { items: [], total: 0, limit: 50, offset: 0, scope_description: "Events involving St Aurelia" } };
      if (url === "/api/audit/verify") return { body: { valid: false, checked_events: 4, head_seq: 4, head_hash: "ab".repeat(32),
        first_invalid_seq: 5, problem: "Content mismatch at seq 5: stored hash does not match the event contents (event modified).",
        verified_at: "2026-10-22T06:00:00Z", notice: "The hash chain is tamper-evident, not tamper-proof." } };
      return undefined;
    };
    const user = userEvent.setup();
    renderAt("/audit");
    await user.click(await screen.findByRole("button", { name: /verify integrity/i }));
    expect(await screen.findByText(/integrity check failed at event 5/i)).toBeInTheDocument();
    expect(screen.getByText(/event modified/i)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/audit/verify")!.init!.method).toBe("POST");
  });
});
