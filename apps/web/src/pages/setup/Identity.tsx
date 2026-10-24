import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../../api/http";
import { Button, Card } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function Identity() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const maps = useList("/idp/mappings");
  const teams = useList("/teams");
  const [log, setLog] = useState<Row | null>(null);
  const sync = async () => {
    try {
      setLog(await api.post<Row>(w(ws, "/idp/sync"), {}));
    } catch (e) {
      toasts.error(e);
    }
  };
  return (
    <>
      <Card
        title="Active Directory groups → roles and teams"
        actions={<Button onClick={sync}>Sync now</Button>}
      >
        <CreateForm
          path="/idp/mappings"
          refresh={["/idp/mappings"]}
          label="Add mapping"
          fields={[
            { name: "provider", label: "Provider", options: ["ldap", "oidc"] },
            { name: "group", label: "Group DN / id", required: true },
            {
              name: "role",
              label: "Role",
              options: ["manager", "contributor", "viewer", "project_owner"],
            },
            { name: "team", label: "Team", options: (teams.data ?? []).map((t) => t.slug) },
          ]}
        />
        <Table
          rows={maps.data ?? []}
          cols={[
            { key: "provider", label: "Provider" },
            { key: "group", label: "Group" },
            { key: "role_key", label: "Role" },
            {
              key: "id",
              label: "",
              render: (r) => (
                <Button
                  variant="ghost"
                  aria-label="delete mapping"
                  onClick={async () => {
                    await api.del(w(ws, `/idp/mappings/${r.id}`));
                    await qc.invalidateQueries({ queryKey: [ws, "/idp/mappings"] });
                  }}
                >
                  ✕
                </Button>
              ),
            },
          ]}
        />
        {log && (
          <pre aria-label="Last sync">{`checked ${log.checked} · changed ${log.changed} · missing ${log.missing}\n${(log.log as string[]).join("\n")}`}</pre>
        )}
        <p className="muted">Kerberos single sign-on is not available in this build.</p>
      </Card>
    </>
  );
}
