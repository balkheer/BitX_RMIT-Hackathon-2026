import { useTideCloak } from "@tidecloak/react";
import { useCallback, useEffect, useEffectEvent, useState } from "react";
import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;
  correlationId: string | null;

  constructor(status: number, body: Partial<ApiErrorBody> | null) {
    super(body?.error?.message ?? `Request failed (${status})`);
    this.status = status;
    this.code = body?.error?.code ?? "HTTP_ERROR";
    this.details = body?.error?.details ?? null;
    this.correlationId = body?.error?.correlation_id ?? null;
  }
}

/** Returns a fetch function that attaches a fresh TideCloak access token to every API call. */
export function useApi() {
  const { getToken, triggerReauth } = useTideCloak();

  return useCallback(
    async <T,>(path: string, init: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> => {
      const token = await getToken(); // refreshes silently when close to expiry
      if (!token) {
        triggerReauth();
        throw new ApiError(401, { error: { code: "INVALID_TOKEN", message: "Your session has ended. Sign in again." } });
      }
      const res = await fetch(`/api${path}`, {
        method: init.method ?? "GET",
        signal: init.signal,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
        credentials: "omit",
        cache: "no-store",
      });
      if (res.status === 204) return undefined as T;
      const data: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        if (res.status === 401) triggerReauth();
        throw new ApiError(res.status, data as ApiErrorBody | null);
      }
      return data as T;
    },
    [getToken, triggerReauth],
  );
}

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Loads `path` on mount and whenever it changes. Pass null to skip. */
export function useResource<T>(path: string | null): Resource<T> {
  const api = useApi();
  const [nonce, setNonce] = useState(0);
  const key = path === null ? null : `${path}#${nonce}`;
  const [state, setState] = useState<{ key: string | null; data: T | null; error: ApiError | null }>({
    key: null, data: null, error: null,
  });
  const load = useEffectEvent((p: string, signal: AbortSignal) => api<T>(p, { signal }));

  useEffect(() => {
    if (path === null || key === null) return;
    const ctrl = new AbortController();
    load(path, ctrl.signal)
      .then((data) => setState({ key, data, error: null }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        const e = err instanceof ApiError ? err : new ApiError(0, { error: { code: "NETWORK", message: "The server could not be reached." } });
        setState({ key, data: null, error: e });
      });
    return () => ctrl.abort();
  }, [path, key]);

  const settled = state.key === key;
  return {
    // Keep showing the previous data while a reload is in flight, but never data from a different path.
    data: state.key?.split("#")[0] === path ? state.data : null,
    error: settled ? state.error : null,
    loading: key !== null && !settled,
    reload: () => setNonce((n) => n + 1),
  };
}
