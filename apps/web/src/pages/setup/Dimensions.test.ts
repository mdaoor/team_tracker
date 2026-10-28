import { expect, test } from "vitest";
import { parseValues } from "./Dimensions";

test("pasted values split on lines and commas, drop blanks and quotes, keep first occurrence", () => {
  expect(parseValues('alpha, "beta"\ngamma\n\n  alpha ;delta')).toEqual([
    "alpha",
    "beta",
    "gamma",
    "delta",
  ]);
  expect(parseValues("  \n ,")).toEqual([]);
});
