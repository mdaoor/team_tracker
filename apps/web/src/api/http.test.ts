import { afterEach, expect, test, vi } from "vitest";
import { api } from "./http";

afterEach(() => vi.restoreAllMocks());

test("postBlob reads the response body exactly once (UX review 1)", async () => {
  let blobCalls = 0;
  let cloneCalls = 0;
  const blob = new Uint8Array([37, 80, 68, 70]);
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
    const res = new Response(blob, { status: 200, headers: { "Content-Type": "application/pdf" } });
    const origBlob = res.blob.bind(res);
    res.blob = () => (blobCalls++, origBlob());
    const origClone = res.clone.bind(res);
    res.clone = () => (cloneCalls++, origClone());
    return res;
  });
  const out = await api.postBlob("/api/v1/w/acme/presentations/x/render?format=pdf", {});
  expect(out.size).toBe(4);
  expect(cloneCalls).toBe(0);
  expect(blobCalls).toBeLessThanOrEqual(1);
});

test("postBlob turns an error response into an ApiError with the request id", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(JSON.stringify({ error: { code: "forbidden", message: "nope" } }), {
        status: 403,
        headers: { "Content-Type": "application/json", "X-Request-Id": "req-1" },
      }),
  );
  await expect(api.postBlob("/x", {})).rejects.toMatchObject({
    status: 403,
    code: "forbidden",
    requestId: "req-1",
  });
});
