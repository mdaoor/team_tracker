import { describe, expect, test } from "vitest";
import { blankTemplate, errorsAt, moveSlide, parseBody, placeholdersIn } from "./templates";

describe("template editor helpers", () => {
  test("JSON syntax errors come back as a pointer + message", () => {
    expect(parseBody("{").errors[0]).toMatchObject({ pointer: "/" });
    expect(parseBody("[1]").body).toBeNull();
    expect(parseBody('{"a":1}').body).toEqual({ a: 1 });
  });
  test("errors are scoped to the slide they belong to", () => {
    const errs = [
      { pointer: "/slides/1/kind", message: "unknown" },
      { pointer: "/slides/10/kind", message: "other" },
      { pointer: "/key", message: "bad" },
    ];
    expect(errorsAt(errs, "/slides/1")).toEqual([errs[0]]);
  });
  test("slides reorder and out-of-range moves are ignored", () => {
    const b = { ...blankTemplate(), slides: [{ kind: "a" }, { kind: "b" }, { kind: "c" }] };
    expect(moveSlide(b, 0, 2).slides.map((s: { kind: string }) => s.kind)).toEqual(["b", "c", "a"]);
    expect(moveSlide(b, 0, -1)).toBe(b);
  });
  test("placeholders are found in titles and nested config", () => {
    expect(
      placeholdersIn([
        { title_template: "${project.name}", config: { a: ["${x}", { b: "n ${y} m" }] } },
      ]),
    ).toEqual(["project.name", "x", "y"]);
  });
});
