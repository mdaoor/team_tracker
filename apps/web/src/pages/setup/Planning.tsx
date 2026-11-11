import { useState, type ReactNode } from "react";
import { MonthGrid, spanDays, type DayEvent } from "../../components/MonthGrid";
import { Card } from "../../components/ui";
import { useList } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function Planning() {
  const projects = useList("/projects");
  const ms = useList("/milestones");
  const its = useList("/iterations");
  const keys = (projects.data ?? []).map((p) => p.key);
  const events: Record<string, DayEvent[]> = {};
  for (const m of ms.data ?? [])
    (events[m.due_date] ??= []).push({ kind: "milestone", label: `◆ ${m.project_key} ${m.name}` });
  for (const it of its.data ?? [])
    for (const d of spanDays(it.start_date, it.end_date))
      (events[d] ??= []).push({
        kind: "iteration",
        label: d === it.start_date ? `▸ ${it.project_key} ${it.name}` : "",
        title: `${it.project_key} ${it.name} (${it.start_date} – ${it.end_date})`,
      });
  return (
    <>
      <Card title="Calendar">
        <MonthGrid events={events} label="Milestones and iterations" />
      </Card>
      <Card title="Milestones">
        <ProjectForm
          kind="milestones"
          keys={keys}
          fields={[
            { name: "name", label: "Name", required: true },
            { name: "due_date", label: "Due", type: "date", required: true },
          ]}
        />
        <Table
          rows={ms.data ?? []}
          cols={[
            { key: "project_key", label: "Project" },
            { key: "name", label: "Name" },
            { key: "due_date", label: "Due" },
          ]}
        />
      </Card>
      <Card title="Iterations">
        <ProjectForm
          kind="iterations"
          keys={keys}
          fields={[
            { name: "name", label: "Name", required: true },
            { name: "start_date", label: "Start", type: "date", required: true },
            { name: "end_date", label: "End", type: "date", required: true },
          ]}
        />
        <Table
          rows={its.data ?? []}
          cols={[
            { key: "project_key", label: "Project" },
            { key: "name", label: "Name" },
            { key: "start_date", label: "Start" },
            { key: "end_date", label: "End" },
          ]}
        />
      </Card>
    </>
  );
}

function ProjectForm({
  kind,
  keys,
  fields,
}: {
  kind: string;
  keys: string[];
  fields: { name: string; label: string; type?: string; required?: boolean }[];
}) {
  // the project is part of the URL, so the form is keyed on a select held outside CreateForm's state
  return (
    <ProjectPicker
      keys={keys}
      render={(k) => (
        <CreateForm
          key={k}
          path={`/projects/${k}/${kind}`}
          refresh={["/milestones", "/iterations"]}
          label={`Add ${kind.slice(0, -1)}`}
          fields={fields}
        />
      )}
    />
  );
}

function ProjectPicker({ keys, render }: { keys: string[]; render: (k: string) => ReactNode }) {
  const [k, setK] = useState("");
  return (
    <>
      <label>
        Project{" "}
        <select value={k} onChange={(e) => setK(e.target.value)} aria-label="Project">
          <option value="">—</option>
          {keys.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
      </label>
      {k && render(k)}
    </>
  );
}
