import type { ReactNode } from "react";

export type TreeNode = { id: string; label: ReactNode; children: TreeNode[]; meta?: ReactNode };

/** Build a forest from flat rows: `parentOf(row)` names the parent id (or null for a root). Cycles become roots. */
export function buildForest<T extends { id?: string }>(
  rows: T[],
  idOf: (r: T) => string,
  parentOf: (r: T) => string | null | undefined,
  make: (r: T, kids: TreeNode[]) => TreeNode,
): TreeNode[] {
  const byId = new Map(rows.map((r) => [idOf(r), r]));
  const kids = new Map<string, T[]>();
  const roots: T[] = [];
  const reaches = (start: string, target: string): boolean => {
    for (let cur: string | null | undefined = start, n = 0; cur && n < 1000; n++) {
      if (cur === target) return true;
      cur = byId.has(cur) ? parentOf(byId.get(cur)!) : null;
    }
    return false;
  };
  for (const r of rows) {
    const p = parentOf(r);
    if (p && byId.has(p) && !reaches(p, idOf(r))) kids.set(p, [...(kids.get(p) ?? []), r]);
    else roots.push(r);
  }
  const build = (r: T): TreeNode => make(r, (kids.get(idOf(r)) ?? []).map(build));
  return roots.map(build);
}

export function Tree({
  nodes,
  label,
  level = 1,
}: {
  nodes: TreeNode[];
  label: string;
  level?: number;
}) {
  if (!nodes.length) return null;
  return (
    <ul
      className="tree"
      role={level === 1 ? "tree" : "group"}
      aria-label={level === 1 ? label : undefined}
    >
      {nodes.map((n) => (
        <li
          key={n.id}
          role="treeitem"
          aria-expanded={n.children.length ? true : undefined}
          aria-level={level}
        >
          <span className="tree-row">
            {n.label}
            {n.meta && <small className="muted"> {n.meta}</small>}
          </span>
          {n.children.length > 0 && <Tree nodes={n.children} label={label} level={level + 1} />}
        </li>
      ))}
    </ul>
  );
}
