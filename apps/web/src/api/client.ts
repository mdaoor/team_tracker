/**
 * Minimal API client. `src/api/schema.d.ts` is generated from docs/api/openapi.json
 * (`make openapi`); richer typed calls arrive with the first business endpoints.
 */
export interface Ready {
  status: "ready" | "not_ready";
  checks: Record<string, string>;
}

export async function getReady(fetchImpl: typeof fetch = fetch): Promise<Ready> {
  const res = await fetchImpl("/readyz");
  // 503 still carries the readiness body
  return (await res.json()) as Ready;
}
