/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * The only module that talks to the network. Everything goes through the generated `openapi-fetch` client; an ESLint rule
 * (`no-restricted-globals: fetch`) keeps the rest of `src/` honest.
 */
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export type Row = Record<string, any>;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public body: any,
    public requestId?: string,
  ) {
    super(message);
  }
}

function cookie(name: string): string {
  return (
    document.cookie
      .split("; ")
      .find((c) => c.startsWith(name + "="))
      ?.split("=")[1] ?? ""
  );
}

export const client = createClient<paths>({
  baseUrl: typeof location === "undefined" ? "http://localhost" : location.origin,
  credentials: "include",
  fetch: (req) => globalThis.fetch(req), // resolved per call so tests can stub it
});
client.use({
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method))
      request.headers.set("X-CSRF-Token", cookie("tt_csrf"));
    return request;
  },
});

type Opts = { query?: Row; body?: unknown; headers?: Record<string, string> };
type Result<T> = { data: T; etag: string | null };

async function call<T>(
  method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE",
  path: string,
  o: Opts = {},
): Promise<Result<T>> {
  // The path is built at run time (workspace slug), so it cannot satisfy the generated `paths` union literally.
  const fn = (client as any)[method] as (
    p: string,
    init: any,
  ) => Promise<{ data?: any; error?: any; response: Response }>;
  const { data, error, response } = await fn(path, {
    params: { query: o.query },
    body: o.body,
    headers: o.headers,
    parseAs: "text",
  });
  const requestId = response.headers.get("X-Request-Id") ?? undefined;
  const text = (data ?? error ?? "") as string;
  let parsed: any = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!response.ok) {
    const e = parsed?.error ?? {};
    throw new ApiError(
      response.status,
      e.code ?? "error",
      e.message ?? `HTTP ${response.status}`,
      parsed,
      requestId,
    );
  }
  return { data: parsed as T, etag: response.headers.get("ETag") };
}

export const api = {
  get: <T = Row>(path: string, query?: Row) => call<T>("GET", path, { query }).then((r) => r.data),
  getWithEtag: <T = Row>(path: string, query?: Row) => call<T>("GET", path, { query }),
  post: <T = Row>(path: string, body?: unknown) =>
    call<T>("POST", path, { body }).then((r) => r.data),
  patch: <T = Row>(path: string, body: unknown, headers?: Record<string, string>) =>
    call<T>("PATCH", path, { body, headers }).then((r) => r.data),
  put: <T = Row>(path: string, body: unknown, headers?: Record<string, string>) =>
    call<T>("PUT", path, { body, headers }).then((r) => r.data),
  postBlob: async (path: string, body: unknown): Promise<Blob> => {
    const { response } = await (client as any).POST(path, { body, parseAs: "blob" });
    if (!response.ok) throw new ApiError(response.status, "error", `HTTP ${response.status}`, null);
    return response.clone().blob();
  },
  del: (path: string) => call<null>("DELETE", path).then((r) => r.data),
};

export const w = (ws: string, path: string) => `/api/v1/w/${ws}${path}`;

export interface Ready {
  status: "ready" | "not_ready";
  checks: Record<string, string>;
}

/** 503 still carries the readiness body, so this does not throw on it. */
export async function getReady(): Promise<Ready> {
  const { data, error } = await (client as any).GET("/readyz", { parseAs: "json" });
  return (data ?? error) as Ready;
}
