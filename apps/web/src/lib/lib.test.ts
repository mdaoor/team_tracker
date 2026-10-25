import { afterEach, expect, test, vi } from "vitest";
import { confidenceCue, severityColor, stageAgeColor } from "./confidence";
import { patchWithEtag } from "./conflict";
import { suggestedChoice } from "../pages/ReviewQueue";

afterEach(() => vi.restoreAllMocks());

test("confidence cue: ✓ / percent / suggested / ask", () => {
  expect(confidenceCue(0.5, true)).toEqual({ kind: "confirmed", label: "✓" });
  expect(confidenceCue(0.92)).toEqual({ kind: "percent", label: "92 %" });
  expect(confidenceCue(0.5).kind).toBe("suggested");
  expect(confidenceCue(0.1).kind).toBe("ask");
  expect(confidenceCue(null).kind).toBe("ask");
});

test("severity colours map to the prototype tokens and stage age warns at 1x and 2x", () => {
  expect(severityColor.critical).toBe("var(--crit)");
  expect(severityColor.serious).toBe("var(--serious)");
  expect(stageAgeColor(1, 3)).toBe("var(--good)");
  expect(stageAgeColor(3, 3)).toBe("var(--warn)");
  expect(stageAgeColor(6, 3)).toBe("var(--crit)");
  expect(stageAgeColor(5, null)).toBe("var(--text-3)");
});

test("PATCH carries If-Match and a 409 becomes a conflict with the current value", async () => {
  const seen: Request[] = [];
  vi.stubGlobal("fetch", async (req: Request) => {
    seen.push(req.clone());
    if (seen.length === 1)
      return Response.json(
        {
          error: {
            code: "conflict",
            message: "changed",
            current: { priority: "high" },
            etag: '"v2"',
          },
        },
        { status: 409 },
      );
    return Response.json({ priority: "low" });
  });
  const first = await patchWithEtag("/api/v1/w/acme/items/WEB-1", { priority: "low" }, '"v1"');
  expect(seen[0].headers.get("If-Match")).toBe('"v1"');
  expect(first.ok).toBe(false);
  if (!first.ok) {
    expect(first.conflict.current.priority).toBe("high");
    // "Keep mine" retries with the etag the server sent
    const retry = await patchWithEtag(
      "/api/v1/w/acme/items/WEB-1",
      { priority: "low" },
      first.conflict.etag,
    );
    expect(retry.ok).toBe(true);
    expect(seen[1].headers.get("If-Match")).toBe('"v2"');
  }
});

test("suggested choice comes from the gateway suggestion first, then from the row's own hint", () => {
  expect(
    suggestedChoice({
      kind: "unclassified_project",
      suggestion: { llm: { suggestions: [{ choice: { project: "WEB" } }] } },
    }),
  ).toEqual({ project: "WEB" });
  expect(suggestedChoice({ kind: "stage_suggestion", suggestion: { stage: "test" } })).toEqual({
    stage: "test",
  });
  expect(suggestedChoice({ kind: "unconfirmed_classification", suggestion: {} })).toEqual({
    action: "confirm",
  });
  expect(suggestedChoice({ kind: "unknown_person", suggestion: {} })).toBeNull();
});

import { undecodable } from "./viewState";
test("a garbage ?f= is reported, a good one is not", () => {
  expect(undecodable("garbage")).toBe(true);
  expect(undecodable(null)).toBe(false);
  expect(undecodable(btoa(JSON.stringify([{ id: "a" }])))).toBe(false);
});
