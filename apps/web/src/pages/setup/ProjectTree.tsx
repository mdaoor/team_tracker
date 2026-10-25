import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Json } from "../../api/http";
import { Tree, buildForest, type TreeNode } from "../../components/Tree";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { moveTarget } from "../../lib/tree";

/**
 * Product → project → epic (UX §3.3). Products reparent by drag-and-drop or with ⌥← (outdent) / ⌥→ (indent under the
 * previous sibling); every move is one PATCH, so the change is an event like any other.
 */
export function ProjectTree() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const products = useList("/products");
  const projects = useList("/projects");
  const prods = products.data ?? [];
  const links = useQueries({
    queries: prods.map((p) => ({
      queryKey: [ws, `/products/${p.key}/links`],
      queryFn: async () => ((await api.get<Json>(w(ws, `/products/${p.key}/links`))).data ?? []) as Json[],
    })),
  });
  const epics = useQuery({
    queryKey: [ws, "setup-epics"],
    queryFn: () => api.post<Json>(w(ws, "/query/items"), { filter: { type: { in: ["epic"] } }, page: { size: 200 } }),
  });
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const projectById = new Map((projects.data ?? []).map((p) => [p.id, p]));
  const epicsBy = new Map<string, Json[]>();
  for (const e of (epics.data?.items ?? []) as Json[])
    epicsBy.set(e.project, [...(epicsBy.get(e.project) ?? []), e]);

  const reparent = async (key: string, parentId: string | null) => {
    try {
      await api.patch(w(ws, `/products/${key}`), { parent_product_id: parentId });
      toasts.push(`Moved ${key}`);
      await qc.invalidateQueries({ queryKey: [ws, "/products"] });
    } catch (e) {
      toasts.error(e);
    }
  };
  const keyMove = (e: React.KeyboardEvent, p: Json) => {
    if (!e.altKey || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    e.preventDefault();
    const target = moveTarget(prods as { id: string; parent_product_id?: string | null }[], p as { id: string; parent_product_id?: string | null }, e.key === "ArrowRight" ? "in" : "out");
    if (target !== undefined) void reparent(p.key, target);
    else toasts.push(e.key === "ArrowRight" ? "Nothing above it to nest under" : "Already top-level", { key: "tree-move" });
  };

  const projectsOf = (productKey: string): TreeNode[] => {
    const idx = prods.findIndex((p) => p.key === productKey);
    const rows = ((links[idx]?.data ?? []) as Json[]).filter((l) => l.target_type === "project");
    return rows.flatMap((l) => {
      const proj = projectById.get(l.target_id);
      if (!proj) return [];
      return [
        {
          id: `${productKey}/${proj.key}`,
          label: <span><strong>{proj.key}</strong> {proj.name}</span>,
          children: (epicsBy.get(proj.key) ?? []).map((e) => ({
            id: e.key,
            label: <span><span className="key">{e.key}</span> {e.title}</span>,
            children: [],
          })),
        } as TreeNode,
      ];
    });
  };

  const forest = buildForest(
    prods,
    (p) => p.id,
    (p) => p.parent_product_id,
    (p, kids) => ({
      id: p.id,
      label: (
        <button
          className={`tree-product${over === p.id ? " drop" : ""}`}
          draggable
          onDragStart={() => setDragging(p.key)}
          onDragOver={(e) => (e.preventDefault(), setOver(p.id))}
          onDragLeave={() => setOver(null)}
          onDrop={() => {
            setOver(null);
            if (dragging && dragging !== p.key) void reparent(dragging, p.id);
            setDragging(null);
          }}
          onKeyDown={(e) => keyMove(e, p)}
          aria-label={`Product ${p.name}. Alt+Right indents under the previous product, Alt+Left moves it out.`}
        >
          ▣ {p.name}
        </button>
      ),
      children: [...kids, ...projectsOf(p.key)],
    }),
  );
  return (
    <>
      {forest.length === 0 ? (
        <p className="muted">Add a product, link projects to it, and they appear here with their epics.</p>
      ) : (
        <Tree nodes={forest} label="Products, projects and epics" />
      )}
      {dragging && (
        <p className="muted">
          Drop on a product to nest {dragging} under it.{" "}
          <button className="linkbtn" onClick={() => void reparent(dragging, null).then(() => setDragging(null))}>
            Make top-level
          </button>
        </p>
      )}
    </>
  );
}
