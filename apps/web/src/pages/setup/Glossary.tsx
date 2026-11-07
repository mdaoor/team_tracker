import { useQueryClient } from "@tanstack/react-query";
import { deleteGlossary, markGlossaryManual } from "../../api/teams";
import { useConfirm } from "../../components/Confirm";
import { useToasts } from "../../components/Toasts";
import { Button, Card } from "../../components/ui";
import { useList, useWs } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function Glossary() {
  const ws = useWs();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const toasts = useToasts();
  const tags = useList("/tags");
  const gloss = useList("/glossary");
  const projects = useList("/projects");
  const byKey = Object.fromEntries((projects.data ?? []).map((p) => [p.key, p.id]));
  const refresh = () => qc.invalidateQueries({ queryKey: [ws, "/glossary"] });
  const prov = (tags.data ?? []).filter((t) => t.is_provisional);
  return (
    <>
      <Card title="Glossary">
        <CreateForm
          path="/glossary"
          refresh={["/glossary"]}
          label="Add phrase"
          transform={(v) => ({
            phrase: v.phrase,
            resolves_to_type: "project",
            resolves_to_id: byKey[v.project],
          })}
          fields={[
            { name: "phrase", label: "Phrase", required: true },
            { name: "project", label: "Project", options: Object.keys(byKey), required: true },
          ]}
        />
        <Table
          rows={gloss.data ?? []}
          cols={[
            { key: "phrase", label: "Phrase" },
            { key: "resolves_to_type", label: "Type" },
            { key: "source", label: "Source" },
            { key: "hits", label: "Hits" },
            {
              key: "id",
              label: "",
              render: (r) => (
                <>
                  {r.source === "learned" && (
                    <Button
                      onClick={async () => {
                        await markGlossaryManual(ws, r.id);
                        await refresh();
                      }}
                    >
                      Promote to manual
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    aria-label={`delete ${r.phrase}`}
                    onClick={async () => {
                      if (
                        !(await confirm({
                          title: `Delete “${r.phrase}”?`,
                          body: "Reporters' lines that use this phrase are no longer placed automatically.",
                        }))
                      )
                        return;
                      try {
                        await deleteGlossary(ws, r.id);
                        await refresh();
                      } catch (e) {
                        toasts.error(e);
                      }
                    }}
                  >
                    Delete
                  </Button>
                </>
              ),
            },
          ]}
        />
      </Card>
      <Card title={`Tags (${prov.length} provisional)`}>
        <Table
          rows={tags.data ?? []}
          cols={[
            { key: "name", label: "Tag" },
            {
              key: "is_provisional",
              label: "Provisional",
              render: (r) => (r.is_provisional ? "yes" : ""),
            },
          ]}
        />
      </Card>
    </>
  );
}
