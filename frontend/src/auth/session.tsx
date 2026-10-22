import { useTideCloak } from "@tidecloak/react";
import { LogIn, ShieldCheck } from "lucide-react";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useResource } from "../api/client";
import type { Me } from "../api/types";
import { ErrorState, Loading } from "../components/ui";

const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe must be used inside <RequireSession>");
  return me;
}

export const can = (me: Me, permission: string) => me.permissions.includes(permission);

/** Brand panel shared by the sign-in, redirect and error screens. */
function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <main className="auth-frame">
      <div className="auth-panel">
        <div className="brand brand-large">
          <BrandMark />
          <span>ConsentBridge</span>
        </div>
        {children}
      </div>
      <p className="auth-footnote">Development and demonstration data is entirely synthetic. No real patient records are used.</p>
    </main>
  );
}

export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" />
      <path d="M7 21h6v-6h6v-6h6" />
    </svg>
  );
}

export function SignInScreen() {
  const { login, isLoading, initError, sessionExpired } = useTideCloak();
  const expired = sessionExpired || sessionStorage.getItem("cb.sessionExpired") === "true";
  return (
    <AuthFrame>
      <h1 className="auth-title">Share health data without blind trust</h1>
      <p className="auth-copy">
        Sign in with your organisation's TideCloak account. Your identity lets you in; every disclosure is still
        checked against your role, your organisation, the purpose you declare and the patient's recorded conditions.
      </p>
      {expired && (
        <p className="auth-alert" role="status">Your session ended. Sign in again to continue.</p>
      )}
      {initError ? (
        <p className="auth-alert" role="alert">
          Sign-in is unavailable: the identity provider configuration could not be loaded. An administrator needs to
          publish this deployment's TideCloak adapter file.
        </p>
      ) : (
        <button
          type="button"
          className="btn btn-primary btn-wide"
          disabled={isLoading}
          onClick={() => { sessionStorage.removeItem("cb.sessionExpired"); void login(); }}
        >
          <LogIn aria-hidden size={18} />
          {isLoading ? "Opening TideCloak…" : "Sign in with TideCloak"}
        </button>
      )}
      <ul className="auth-points">
        <li><ShieldCheck aria-hidden size={16} />Only the fields a purpose needs are released</li>
        <li><ShieldCheck aria-hidden size={16} />Sensitive requests go to a privacy reviewer</li>
        <li><ShieldCheck aria-hidden size={16} />Every decision is written to a tamper-evident audit trail</li>
      </ul>
    </AuthFrame>
  );
}

/** Route target for TideCloak's redirect (`${origin}/auth/redirect`). */
export function AuthRedirect() {
  const { authenticated, isInitializing, logout } = useTideCloak();
  const navigate = useNavigate();
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("auth") === "failed") {
      sessionStorage.setItem("cb.sessionExpired", "true");
      void logout();
      return;
    }
    if (!isInitializing) navigate(authenticated ? "/" : "/sign-in", { replace: true });
  }, [authenticated, isInitializing, logout, navigate]);
  return <AuthFrame><Loading label="Completing sign-in" /></AuthFrame>;
}

/**
 * Protected-route gate. Requires an authenticated TideCloak session AND a ConsentBridge identity mapping
 * (confirmed by the server via /api/me). There is no alternative path in.
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const { authenticated, isInitializing, needsReauth, sessionExpired } = useTideCloak();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isInitializing && (!authenticated || sessionExpired)) {
      if (sessionExpired) sessionStorage.setItem("cb.sessionExpired", "true");
      navigate("/sign-in", { replace: true });
    }
  }, [authenticated, isInitializing, sessionExpired, navigate]);

  if (isInitializing) return <AuthFrame><Loading label="Checking your session" /></AuthFrame>;
  if (!authenticated) return null;
  if (needsReauth) return <ReauthPrompt />;
  return <MeLoader>{children}</MeLoader>;
}

function MeLoader({ children }: { children: ReactNode }) {
  const me = useResource<Me>("/me");
  const { logout, getValueFromToken } = useTideCloak();
  if (me.loading) return <AuthFrame><Loading label="Loading your workspace" /></AuthFrame>;
  if (me.error || !me.data) {
    return (
      <AuthFrame>
        {me.error && <ErrorState error={me.error} onRetry={me.reload} />}
        {me.error?.code === "IDENTITY_NOT_MAPPED" && (
          <p className="small">
            Give this identifier to a platform administrator so they can link your account:{" "}
            <code className="hash">{String(getValueFromToken("sub") ?? "unavailable")}</code>
          </p>
        )}
        <button type="button" className="btn btn-secondary btn-wide" onClick={() => void logout()}>Sign out</button>
      </AuthFrame>
    );
  }
  return <MeContext.Provider value={me.data}>{children}</MeContext.Provider>;
}

function ReauthPrompt() {
  const { login, clearReauth } = useTideCloak();
  return (
    <AuthFrame>
      <h1 className="auth-title">Sign in again</h1>
      <p className="auth-copy">Your session expired or was rejected by the server. Nothing was released.</p>
      <button type="button" className="btn btn-primary btn-wide" onClick={() => { clearReauth(); void login(); }}>
        <LogIn aria-hidden size={18} /> Sign in with TideCloak
      </button>
    </AuthFrame>
  );
}
