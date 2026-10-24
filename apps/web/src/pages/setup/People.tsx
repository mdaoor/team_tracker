import { Card } from "../../components/ui";
import { useList } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function People() {
  const people = useList("/people");
  const teams = useList("/teams");
  const ids = (people.data ?? []).map((p) => p.person_id);
  const byHandle = Object.fromEntries((people.data ?? []).map((p) => [p.handle, p.person_id]));
  return (
    <>
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
