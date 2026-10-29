type P = { id: string; parent_product_id?: string | null };

/**
 * Where an Alt+Arrow move puts a product: `out` makes it a sibling of its parent; `in` nests it under the previous
 * sibling. Returns the new parent id (null = top level) or `undefined` when the move is impossible.
 */
export function moveTarget<T extends P>(
  all: T[],
  p: T,
  dir: "in" | "out",
): string | null | undefined {
  if (dir === "out") {
    if (!p.parent_product_id) return undefined;
    return all.find((x) => x.id === p.parent_product_id)?.parent_product_id ?? null;
  }
  const sibs = all.filter((x) => (x.parent_product_id ?? null) === (p.parent_product_id ?? null));
  const prev = sibs[sibs.findIndex((x) => x.id === p.id) - 1];
  return prev ? prev.id : undefined;
}

/**
 * Where an Alt+Arrow move puts a project: `out` moves it to the product's parent; `in` nests it under the last child product of
 * its current product (the sibling just above it in the tree). Returns the target product id, or `undefined` when impossible.
 */
export function projectTarget<T extends P>(
  all: T[],
  current: T,
  dir: "in" | "out",
): string | undefined {
  if (dir === "out") return current.parent_product_id || undefined;
  const kids = all.filter((x) => x.parent_product_id === current.id);
  return kids[kids.length - 1]?.id;
}

type E = { id: string; key: string; parent_id?: string | null };

/**
 * Where an Alt+Arrow move puts an epic of one project: `out` makes it top-level in the project; `in` nests it under the epic just above it
 * among its siblings. Returns the new parent's key (null = top level) or `undefined` when the move is impossible.
 */
export function epicTarget<T extends E>(
  siblings: T[],
  e: T,
  dir: "in" | "out",
): string | null | undefined {
  if (dir === "out") return e.parent_id ? null : undefined;
  const same = siblings.filter((x) => (x.parent_id ?? null) === (e.parent_id ?? null));
  return same[same.findIndex((x) => x.id === e.id) - 1]?.key;
}
