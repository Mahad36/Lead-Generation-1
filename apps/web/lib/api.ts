import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./auth";

export const API_URL =
  typeof window === "undefined" ? process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000" : "";

type Options = RequestInit & { auth?: boolean; raw?: boolean };

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg).replace(/^Value error, /i, "");
        }
        return "";
      })
      .filter(Boolean);
    if (messages.length) return messages.join(" ");
  }
  return fallback;
}

async function refreshAccess(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  const res = await fetch(`${API_URL}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) {
    clearTokens();
    return false;
  }
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return true;
}

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const { auth = true, raw, headers, ...rest } = options;
  const buildHeaders = () => {
    const h = new Headers(headers);
    if (!(rest.body instanceof FormData) && !h.has("Content-Type")) {
      h.set("Content-Type", "application/json");
    }
    if (auth) {
      const token = getAccessToken();
      if (token) h.set("Authorization", `Bearer ${token}`);
    }
    return h;
  };

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...rest,
      headers: buildHeaders(),
      signal: rest.signal ?? AbortSignal.timeout(20000),
    });
  } catch {
    throw new Error("Cannot reach the server. Refresh and try again.");
  }
  if (res.status === 401 && auth) {
    const ok = await refreshAccess();
    if (ok) {
      try {
        res = await fetch(`${API_URL}${path}`, {
          ...rest,
          headers: buildHeaders(),
          signal: rest.signal ?? AbortSignal.timeout(20000),
        });
      } catch {
        throw new Error("Cannot reach the server. Refresh and try again.");
      }
    } else if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  }
  if (raw) return res as T;
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = formatApiDetail(body.detail, res.statusText);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("text/csv")) return (await res.text()) as T;
  return (await res.json()) as T;
}

export const authApi = {
  register: (data: { email: string; password: string; full_name: string }) =>
    api("/api/auth/register", { method: "POST", body: JSON.stringify(data), auth: false }),
  login: (data: { email: string; password: string }) =>
    api("/api/auth/login", { method: "POST", body: JSON.stringify(data), auth: false }),
  forgot: (email: string) =>
    api("/api/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }), auth: false }),
  reset: (token: string, password: string) =>
    api("/api/auth/reset-password", { method: "POST", body: JSON.stringify({ token, password }), auth: false }),
  me: () => api("/api/auth/me"),
  logout: (refresh_token: string) =>
    api("/api/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token }) }),
};
