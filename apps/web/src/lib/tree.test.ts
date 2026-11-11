import { expect, test } from "vitest";
import { epicTarget, moveTarget, projectTarget } from "./tree";
import { buildForest } from "../components/Tree";
import { orgForest } from "../pages/setup/People";

const prods = [{ id: "a" }, { id: "b" }, { id: "c", parent_product_id: "b" }];

test("Alt+Right nests under the previous sibling, Alt+Left lifts out to the parent's level", () => {
  expect(moveTarget(prods, prods[1], "in")).toBe("a");
  expect(moveTarget(prods, prods[0], "in")).toBeUndefined(); // nothing above it
  expect(moveTarget(prods, prods[2], "out")).toBeNull(); // b is top-level, so c becomes top-level
  expect(moveTarget(prods, prods[0], "out")).toBeUndefined(); // already top-level
  const deeper = [...prods, { id: "d", parent_product_id: "c" }];
  expect(moveTarget(deeper, deeper[3], "out")).toBe("b");
});

test("buildForest makes cycles into roots instead of hiding or looping", () => {
  const rows = [
    { id: "x", p: "y" },
    { id: "y", p: "x" },
    { id: "z", p: "x" },
  ];
  const f = buildForest(
    rows,
    (r) => r.id,
    (r) => r.p,
    (r, kids) => ({ id: r.id, label: r.id, children: kids }),
  );
  expect(f.length).toBeGreaterThan(0);
  const count = (n: { children: unknown[] }): number =>
    1 + (n.children as { children: unknown[] }[]).reduce((a, c) => a + count(c), 0);
  expect(f.reduce((a, n) => a + count(n), 0)).toBe(3);
});

test("the org tree sums weekly capacity up the reporting lines", () => {
  const people = [
    { person_id: "m", handle: "@boss", display_name: "Boss", capacity_hours_per_week: 30 },
    { person_id: "a", handle: "@a", display_name: "A", capacity_hours_per_week: 40 },
    { person_id: "b", handle: "@b", display_name: "B", capacity_hours_per_week: 20 },
  ];
  const lines = [
    { kind: "line", is_primary: true, person_id: "a", manager_id: "m" },
    { kind: "line", is_primary: true, person_id: "b", manager_id: "m" },
  ];
  const [root] = orgForest(people, lines);
  expect(root.id).toBe("m");
  expect(root.children.map((c) => c.id).sort()).toEqual(["a", "b"]);
  expect(String(root.meta)).toContain("team 90 h/wk");
});

test("a project moves out to the product's parent or in under the last child product", () => {
  expect(projectTarget(prods, prods[2], "out")).toBe("b"); // c sits under b
  expect(projectTarget(prods, prods[1], "out")).toBeUndefined(); // b is top-level
  expect(projectTarget(prods, prods[1], "in")).toBe("c");
  expect(projectTarget(prods, prods[0], "in")).toBeUndefined(); // a has no child product
});

test("an epic goes top-level with Alt+Left and under the previous sibling epic with Alt+Right", () => {
  const eps = [
    { id: "1", key: "W-1" },
    { id: "2", key: "W-2" },
    { id: "3", key: "W-3", parent_id: "1" },
  ];
  expect(epicTarget(eps, eps[1], "in")).toBe("W-1");
  expect(epicTarget(eps, eps[0], "in")).toBeUndefined();
  expect(epicTarget(eps, eps[2], "in")).toBeUndefined(); // only child of W-1
  expect(epicTarget(eps, eps[2], "out")).toBeNull();
  expect(epicTarget(eps, eps[0], "out")).toBeUndefined();
});
