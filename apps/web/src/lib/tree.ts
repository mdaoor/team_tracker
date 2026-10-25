type P = { id: string; parent_product_id?: string | null };

/**
 * Where an Alt+Arrow move puts a product: `out` makes it a sibling of its parent; `in` nests it under the previous
 * sibling. Returns the new parent id (null = top level) or `undefined` when the move is impossible.
 */
export function moveTarget<T extends P>(all: T[], p: T, dir: "in" | "out"): string | null | undefined {
  if (dir === "out") {
    if (!p.parent_product_id) return undefined;
    return all.find((x) => x.id === p.parent_product_id)?.parent_product_id ?? null;
  }
  const sibs = all.filter((x) => (x.parent_product_id ?? null) === (p.parent_product_id ?? null));
  const prev = sibs[sibs.findIndex((x) => x.id === p.id) - 1];
  return prev ? prev.id : undefined;
}
