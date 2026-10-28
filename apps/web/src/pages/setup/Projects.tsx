import { Card, Chip } from "../../components/ui";
import { useList } from "../../lib/hooks";
import { CreateForm, Table } from "./common";
import { ProjectTree } from "./ProjectTree";

export function Projects() {
  const products = useList("/products");
  const projects = useList("/projects");
  const processes = useList("/processes");
  return (
    <>
      <Card title="Products, projects and epics">
        <ProjectTree />
      </Card>
      <Card title="Add project">
        <p className="muted">
          Only a name, a key and a process are needed; everything else has a default.
        </p>
        <CreateForm
          path="/projects"
          refresh={["/projects"]}
          label="Add project"
          fields={[
            { name: "name", label: "Name", required: true },
            { name: "key", label: "Key", required: true, placeholder: "WEB" },
            {
              name: "process",
              label: "Process",
              options: (processes.data ?? []).map((p) => p.key),
            },
          ]}
        />
        <Table
          rows={projects.data ?? []}
          cols={[
            { key: "key", label: "Key" },
            { key: "name", label: "Name" },
            { key: "visibility", label: "Visibility" },
            {
              key: "priority",
              label: "Priority",
              render: (r) => <Chip>{String(r.priority ?? "inherit")}</Chip>,
            },
          ]}
        />
      </Card>
      <Card title="Products">
        <CreateForm
          path="/products"
          refresh={["/products"]}
          label="Add product"
          fields={[
            { name: "key", label: "Key", required: true },
            { name: "name", label: "Name", required: true },
          ]}
        />
        <Table
          rows={products.data ?? []}
          cols={[
            { key: "key", label: "Key" },
            { key: "name", label: "Name" },
          ]}
        />
      </Card>
    </>
  );
}
