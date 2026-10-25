import { expect, test } from "vitest";
import { ago } from "./format";

const now = Date.parse("2026-10-25T12:00:00Z");
test.each([
  ["2026-10-25T11:59:30Z", "just now"],
  ["2026-10-25T11:30:00Z", "30 min ago"],
  ["2026-10-25T10:00:00Z", "2 h ago"],
  ["2026-10-22T12:00:00Z", "3 d ago"],
  [null, ""],
])("ago(%s)", (iso, want) => expect(ago(iso as string | null, now)).toBe(want));
