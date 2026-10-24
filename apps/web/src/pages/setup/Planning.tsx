import { useState, type ReactNode } from "react";
import { Card } from "../../components/ui";
import { useList } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function Planning() {
  const projects = useList("/projects");
  const ms = useList("/milestones");
  const its = useList("/iterations");
  const keys = (projects.data ?? []).map((p) => p.key);
  return (
    <>
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
