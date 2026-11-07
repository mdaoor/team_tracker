import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { queryItems } from "../../api/items";
import {
  linkProject,
  moveItem,
  productLinks,
  reparentProduct,
  unlinkProject,
} from "../../api/teams";
import type { Item, Link, Product, Project } from "../../api/types";
import { Tree, buildForest, type TreeNode } from "../../components/Tree";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { epicTarget, moveTarget, projectTarget } from "../../lib/tree";

type Drag =
  | { kind: "product"; key: string }
  | { kind: "project"; key: string; from: string; link: Link }
  | { kind: "epic"; key: string; project: string };

const EPIC_STAYS =
  "An epic keeps its project: its key, workflow and plan belong to it. Move it inside the project.";

/**
 * Product → project → epic (UX §3.3). Everything moves by drag-and-drop or with ⌥← (outdent) / ⌥→ (indent under the previous sibling):
 * a product is one PATCH; a project is unlinked from its product and linked to the new one (`DELETE` then `POST` of the product
 * link); an epic is one `POST /items/{key}/move` inside its project (to the top, or under another epic when the workspace allows
 * same-level nesting). Every move is an event like any other.
 */
export function ProjectTree() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const products = useList<Product>("/products");
  const projects = useList<Project>("/projects");
  const prods = products.data ?? [];
  const links = useQueries({
    queries: prods.map((p) => ({
      queryKey: [ws, `/products/${p.key}/links`],
      queryFn: async () => (await productLinks(ws, p.key)).data ?? [],
    })),
  });
  const epics = useQuery({
    queryKey: [ws, "setup-epics"],
    queryFn: () => queryItems(ws, { filter: { type: { in: ["epic"] } }, page: { size: 200 } }),
  });
  const [dragging, setDragging] = useState<Drag | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const projectById = new Map((projects.data ?? []).map((p) => [p.id, p]));
  const epicsBy = new Map<string, Item[]>();
  for (const e of epics.data?.items ?? [])
    epicsBy.set(e.project, [...(epicsBy.get(e.project) ?? []), e]);

  const refresh = () =>
    qc.invalidateQueries({
      predicate: (q) =>
        q.queryKey[0] === ws &&
        (q.queryKey[1] === "setup-epics" ||
          (typeof q.queryKey[1] === "string" && q.queryKey[1].startsWith("/products"))),
    });

  const reparent = async (key: string, parentId: string | null) => {
    try {
      await reparentProduct(ws, key, parentId);
      toasts.push(`Moved ${key}`);
      await refresh();
    } catch (e) {
      toasts.error(e);
    }
  };
  /** Unlink from `from`, link under `to` with the same role and weight; the old link is restored when the new one fails. */
  const moveProject = async (projectKey: string, from: string, link: Link, to: Product) => {
    if (from === to.key) return;
    const role = String(link.role ?? "delivers");
    const weight = link.weight == null ? null : Number(link.weight); // the server sends decimals as strings
    try {
      await unlinkProject(ws, from, projectKey);
    } catch (e) {
      toasts.error(e);
      return;
    }
    try {
      await linkProject(ws, to.key, projectKey, role, weight);
      toasts.push(`Moved ${projectKey} to ${to.key}`);
    } catch (e) {
      await linkProject(ws, from, projectKey, role, weight).catch(() => undefined);
      toasts.error(e);
    }
    await refresh();
  };
  const moveEpic = async (key: string, parent: string | null) => {
    try {
      await moveItem(ws, key, parent);
      toasts.push(`Moved ${key}`);
      await refresh();
    } catch (e) {
      toasts.error(e);
    }
  };

  const altArrow = (e: React.KeyboardEvent): "in" | "out" | null => {
    if (!e.altKey || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return null;
    e.preventDefault();
    return e.key === "ArrowRight" ? "in" : "out";
  };
  const impossible = (dir: "in" | "out", what: string) =>
    toasts.push(
      dir === "in" ? `Nothing above it to nest ${what} under` : `${what} is already at the top`,
      { key: "tree-move" },
    );

  const keyMoveProduct = (e: React.KeyboardEvent, p: Product) => {
    const dir = altArrow(e);
    if (!dir) return;
    const target = moveTarget(
      prods as { id: string; parent_product_id?: string | null }[],
      p as { id: string; parent_product_id?: string | null },
      dir,
    );
    if (target !== undefined) void reparent(p.key, target);
    else
      toasts.push(dir === "in" ? "Nothing above it to nest under" : "Already top-level", {
        key: "tree-move",
      });
  };
  const keyMoveProject = (e: React.KeyboardEvent, from: Product, proj: Project, link: Link) => {
    const dir = altArrow(e);
    if (!dir) return;
    const id = projectTarget(prods, from, dir);
    const to = prods.find((p) => p.id === id);
    if (to) void moveProject(proj.key, from.key, link, to);
    else
      toasts.push(
        dir === "in"
          ? "No product inside this one to nest the project under"
          : "Already under a top-level product",
        { key: "tree-move" },
      );
  };
  const keyMoveEpic = (e: React.KeyboardEvent, ep: Item) => {
    const dir = altArrow(e);
    if (!dir) return;
    const sibs = (epicsBy.get(ep.project) ?? []) as (Item & { id: string })[];
    const target = epicTarget(
      sibs as { id: string; key: string; parent_id?: string | null }[],
      ep as { id: string; key: string; parent_id?: string | null },
      dir,
    );
    if (target !== undefined) void moveEpic(ep.key, target);
    else impossible(dir, "the epic");
  };

  const allow = (id: string) => ({
    onDragOver: (e: React.DragEvent) => (e.preventDefault(), setOver(id)),
    onDragLeave: () => setOver(null),
  });
  const cls = (base: string, id: string) => `${base}${over === id ? " drop" : ""}`;
  const drop = (fn: (d: Drag) => void) => () => {
    setOver(null);
    const d = dragging;
    setDragging(null);
    if (d) fn(d);
  };

  const epicNodes = (project: string): TreeNode[] =>
    buildForest(
      (epicsBy.get(project) ?? []) as (Item & { id: string })[],
      (e) => e.id,
      (e) => e.parent_id as string | null | undefined,
      (e, kids) => ({
        id: e.key,
        label: (
          <button
            className={cls("tree-node", e.key)}
            draggable
            onDragStart={() => setDragging({ kind: "epic", key: e.key, project })}
            onDragEnd={() => setDragging(null)}
            {...allow(e.key)}
            onDrop={drop((d) => {
              if (d.kind !== "epic" || d.key === e.key) return;
              if (d.project !== project) toasts.push(EPIC_STAYS);
              else void moveEpic(d.key, e.key);
            })}
            onKeyDown={(ev) => keyMoveEpic(ev, e)}
            aria-label={`Epic ${e.key} ${e.title}. Alt+Right nests under the epic above it, Alt+Left makes it top-level in ${project}.`}
          >
            <span className="key">{e.key}</span> {e.title}
          </button>
        ),
        children: kids,
      }),
    );

  const projectsOf = (product: Product): TreeNode[] => {
    const idx = prods.findIndex((p) => p.key === product.key);
    const rows = (links[idx]?.data ?? []).filter((l: Link) => l.target_type === "project");
    return rows.flatMap((l) => {
      const proj = projectById.get(l.target_id);
      if (!proj) return [];
      const id = `${product.key}/${proj.key}`;
      return [
        {
          id,
          label: (
            <button
              className={cls("tree-node", id)}
              draggable
              onDragStart={() =>
                setDragging({ kind: "project", key: proj.key, from: product.key, link: l })
              }
              onDragEnd={() => setDragging(null)}
              {...allow(id)}
              onDrop={drop((d) => {
                // dropping an epic on its own project makes it top-level there
                if (d.kind !== "epic") return;
                if (d.project === proj.key) void moveEpic(d.key, null);
                else toasts.push(EPIC_STAYS);
              })}
              onKeyDown={(ev) => keyMoveProject(ev, product, proj, l)}
              aria-label={`Project ${proj.key} ${proj.name}. Alt+Right nests it under the last product inside ${product.name}, Alt+Left moves it up to the parent product.`}
            >
              <strong>{proj.key}</strong> {proj.name}
            </button>
          ),
          children: epicNodes(proj.key),
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
          className={cls("tree-product", p.id)}
          draggable
          onDragStart={() => setDragging({ kind: "product", key: p.key })}
          {...allow(p.id)}
          onDrop={drop((d) => {
            if (d.kind === "product" && d.key !== p.key) void reparent(d.key, p.id);
            else if (d.kind === "project") void moveProject(d.key, d.from, d.link, p);
            else if (d.kind === "epic") toasts.push(EPIC_STAYS);
          })}
          onKeyDown={(e) => keyMoveProduct(e, p)}
          aria-label={`Product ${p.name}. Alt+Right indents under the previous product, Alt+Left moves it out.`}
        >
          ▣ {p.name}
        </button>
      ),
      children: [...kids, ...projectsOf(p)],
    }),
  );
  return (
    <>
      {forest.length === 0 ? (
        <p className="muted">
          Add a product, link projects to it, and they appear here with their epics.
        </p>
      ) : (
        <Tree nodes={forest} label="Products, projects and epics" />
      )}
      {dragging?.kind === "product" && (
        <p className="muted">
          Drop on a product to nest {dragging.key} under it.{" "}
          <button
            className="linkbtn"
            onClick={() => void reparent(dragging.key, null).then(() => setDragging(null))}
          >
            Make top-level
          </button>
        </p>
      )}
      {dragging?.kind === "project" && (
        <p className="muted">Drop on a product to move {dragging.key} under it.</p>
      )}
      {dragging?.kind === "epic" && (
        <p className="muted">
          Drop on {dragging.project} to make {dragging.key} top-level, or on another epic of{" "}
          {dragging.project} to nest it (needs same-level nesting).
        </p>
      )}
    </>
  );
}
