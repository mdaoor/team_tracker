import { Card } from "../../components/ui";
import { Tree, buildForest, type TreeNode } from "../../components/Tree";
import type { Json } from "../../api/http";
import { useList } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function People() {
  const people = useList("/people");
  const teams = useList("/teams");
  const ids = (people.data ?? []).map((p) => p.person_id);
  const byHandle = Object.fromEntries((people.data ?? []).map((p) => [p.handle, p.person_id]));
  const lines = useList("/reporting-lines");
  const org = orgForest(people.data ?? [], lines.data ?? []);
  return (
    <>
      <Card title="Org tree">
        {lines.isError || !org.length ? (
          <p className="muted">Reporting lines you can edit appear here as a tree, with each team's weekly capacity.</p>
        ) : (
          <Tree nodes={org} label="Reporting lines" />
        )}
      </Card>
      <Card title="People">
        <CreateForm
          path="/members"
          refresh={["/people"]}
          label="Add person"
          fields={[
            { name: "display_name", label: "Name", required: true },
            { name: "email", label: "E-mail", type: "email", required: true },
            { name: "handle", label: "Handle", required: true, placeholder: "@sara" },
          ]}
        />
        <Table
          rows={people.data ?? []}
          cols={[
            { key: "handle", label: "Handle" },
            { key: "display_name", label: "Name" },
            { key: "status", label: "Status" },
          ]}
        />
      </Card>
      <Card title="Teams">
        <CreateForm
          path="/teams"
          refresh={["/teams"]}
          label="Add team"
          fields={[
            { name: "name", label: "Name", required: true },
            { name: "slug", label: "Slug", required: true },
            { name: "kind", label: "Kind", options: ["org", "virtual"] },
          ]}
        />
        <Table
          rows={teams.data ?? []}
          cols={[
            { key: "slug", label: "Slug" },
            { key: "name", label: "Name" },
            { key: "kind", label: "Kind" },
          ]}
        />
      </Card>
      <Card title="Reporting lines">
        <p className="muted">
          One primary line manager per person; project/functional/mentor/dotted lines need a scope.
        </p>
        <CreateForm
          path="/reporting-lines"
          refresh={["/people"]}
          label="Add line"
          transform={(v) => ({
            ...v,
            person_id: byHandle[v.person] ?? v.person,
            manager_id: byHandle[v.manager] ?? v.manager,
            person: undefined,
            manager: undefined,
            is_primary: v.is_primary === "yes",
          })}
          fields={[
            { name: "person", label: "Person", options: Object.keys(byHandle), required: true },
            { name: "manager", label: "Manager", options: Object.keys(byHandle), required: true },
            {
              name: "kind",
              label: "Kind",
              options: ["line", "senior", "project", "functional", "mentor", "dotted"],
            },
            { name: "is_primary", label: "Primary", options: ["yes", "no"] },
          ]}
        />
        <small>{ids.length} people</small>
      </Card>
    </>
  );
}

/** People under their primary line manager; each node shows its own and its subtree's weekly capacity. */
export function orgForest(people: Json[], lines: Json[]): TreeNode[] {
  const primary = new Map<string, string>();
  for (const l of lines)
    if (l.kind === "line" && (l.is_primary || !primary.has(l.person_id))) primary.set(l.person_id, l.manager_id);
  const cap = (id: string) => Number(people.find((p) => p.person_id === id)?.capacity_hours_per_week ?? 0);
  const total = (n: TreeNode & { own?: number }): number => (n as { own: number }).own + n.children.reduce((a, c) => a + total(c), 0);
  return buildForest(
    people.filter((p) => primary.has(p.person_id) || lines.some((l) => l.manager_id === p.person_id)),
    (p) => p.person_id,
    (p) => primary.get(p.person_id),
    (p, kids) => {
      const node = {
        id: p.person_id as string,
        label: <strong>{p.handle}</strong>,
        children: kids,
        own: cap(p.person_id),
      } as TreeNode & { own: number };
      node.meta = `${p.display_name} · ${node.own || "–"} h/wk${kids.length ? ` · team ${total(node)} h/wk` : ""}`;
      return node;
    },
  );
}
