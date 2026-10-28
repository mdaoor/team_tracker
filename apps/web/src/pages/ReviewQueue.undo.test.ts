import { describe, expect, it } from "vitest";
import { REVIEW_UNDO_MS } from "./ReviewQueue";

describe("review resolve toast", () => {
  it("offers Undo for 3 s as the plan says (R1.4)", () => {
    expect(REVIEW_UNDO_MS).toBe(3000);
  });
});
