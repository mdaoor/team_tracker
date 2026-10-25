import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../api/http";
import { patchWithEtag, type Conflict } from "../lib/conflict";
import { Drawer } from "./Drawer";
import { ConflictDialog } from "./ConflictDialog";
import { useToasts } from "./Toasts";
import { Button, Empty, ProvenanceChip } from "./ui";

const PRIORITIES = ["lowest", "low", "medium", "high", "critical"];
type Tab = "overview" | "activity" | "evidence" | "placements" | "violations" | "events";

export function ItemDrawer({
  ws,
  itemKey,
  onClose,
}: {
  ws: string;
  itemKey: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const toasts = useToasts();
  const [tab, setTab] = useState<Tab>("overview");
  const [conflict, setConflict] = useState<(Conflict & { field: string; mine: unknown }) | null>(
    null,
  );
  const item = useQuery({
    queryKey: [ws, "item", itemKey],
    queryFn: () => api.getWithEtag<Row>(w(ws, `/items/${itemKey}`)),
  });
  const prov = useQuery({
    queryKey: [ws, "prov", itemKey],
    queryFn: () => api.get<Row>(w(ws, `/items/${itemKey}/provenance`)),
  });
  const placements = useQuery({
    queryKey: [ws, "placements", itemKey],
    queryFn: () => api.get<Row>(w(ws, `/items/${itemKey}/placements`)),
    enabled: tab === "placements",
  });
  const events = useQuery({
    queryKey: [ws, "events", itemKey],
    queryFn: () => api.get<Row>(w(ws, `/items/${itemKey}/events`)),
    enabled: tab === "events",
  });
  const it = item.data?.data;
  const violations = useQuery({
    queryKey: [ws, "item-violations", it?.id],
    queryFn: () => api.get<Row>(w(ws, "/violations"), { target_id: it?.id, limit: 100 }),
    enabled: tab === "violations" && !!it?.id,
  });

  const save = async (field: string, value: unknown, etag?: string) => {
    if (!item.data) return;
    const before = it?.[field];
    const res = await patchWithEtag(
      w(ws, `/items/${itemKey}`),
      { [field]: value },
      etag ?? item.data.etag ?? "",
    );
    if (!res.ok) {
      setConflict({ ...res.conflict, field, mine: value });
      return;
    }
    await qc.invalidateQueries({ queryKey: [ws, "item", itemKey] });
    await qc.invalidateQueries({ queryKey: [ws, "prov", itemKey] });
    toasts.push(`${field} → ${String(value)}`, { key: `edit:${field}`, undo: () => void save(field, before) });
  };

  return (
    <Drawer open onClose={onClose} title={it ? `${it.key} · ${it.title}` : itemKey}>
      {item.isError && <p role="alert">This item does not exist or you may not see it.</p>}
      {it && (
        <>
          <p className="muted">
            reported by {it.reporter ?? "a former member"} · {it.state} · stage {it.stage ?? "—"}{" "}
            · {it.hours_logged} h logged
          </p>
          <div role="tablist" className="tabs">
            {(
              ["overview", "activity", "evidence", "placements", "violations", "events"] as Tab[]
            ).map((x) => (
              <button key={x} role="tab" aria-selected={tab === x} onClick={() => setTab(x)}>
                {x}
              </button>
            ))}
          </div>
          {conflict && (
            <ConflictDialog
              field={conflict.field}
              mine={conflict.mine}
              theirs={conflict.current[conflict.field]}
              changedBy={conflict.changedBy}
              changedAt={conflict.changedAt}
              onKeepMine={async () => {
                const f = conflict.field;
                const v = conflict.mine;
                const etag = conflict.etag;
                setConflict(null);
                await save(f, v, etag);
              }}
              onTakeTheirs={async () => {
                setConflict(null);
                await qc.invalidateQueries({ queryKey: [ws, "item", itemKey] });
              }}
              onRevert={async () => {
                setConflict(null);
                await qc.invalidateQueries({ queryKey: [ws, "item", itemKey] });
              }}
            />
          )}
          {tab === "overview" && (
            <dl className="props">
              <dt>Priority</dt>
              <dd>
                <select
                  aria-label="Priority"
                  value={it.priority}
                  onChange={(e) => save("priority", e.target.value)}
                >
                  {PRIORITIES.map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
                <ProvenanceChip
                  source={String(it.priority_source ?? "manual")}
                  setBy={prov.data?.priority?.[0]?.set_by_name}
                  setAt={prov.data?.priority?.[0]?.set_at}
                />
              </dd>
              <dt>Impact</dt>
              <dd>
                <select
                  aria-label="Impact"
                  value={it.impact ?? ""}
                  onChange={(e) => save("impact", Number(e.target.value))}
                >
                  <option value="" disabled>
                    —
                  </option>
                  {[1, 2, 3, 4, 5].map((n) => (
                    <option key={n}>{n}</option>
                  ))}
                </select>
              </dd>
              <dt>Weight</dt>
              <dd>{String(it.weight)}</dd>
              <dt>Due</dt>
              <dd>{it.due_date ?? "—"}</dd>
              {Object.entries(prov.data ?? {})
                .filter(
                  ([k, v]) => !["priority", "impact", "assignee"].includes(k) && Array.isArray(v),
                )
                .map(([dim, vals]) => (
                  <div key={dim} className="dimrow">
                    <dt>{dim}</dt>
                    <dd>
                      {(vals as Row[]).map((v) => (
                        <span key={v.value}>
                          {v.value}{" "}
                          <ProvenanceChip
                            source={v.source}
                            setBy={v.set_by_name}
                            setAt={v.set_at}
                            inheritedFrom={v.inherited_from ? (v.inherited_from.name ?? v.inherited_from.type) : null}
                          />{" "}
                        </span>
                      ))}
                    </dd>
                  </div>
                ))}
            </dl>
          )}
          {tab === "activity" &&
            (it.latest_activity ? (
              <pre>{JSON.stringify(it.latest_activity, null, 1)}</pre>
            ) : (
              <Empty>No activity yet.</Empty>
            ))}
          {tab === "evidence" && <p>{it.evidence_count} evidence item(s).</p>}
          {tab === "placements" && (
            <table>
              <tbody>
                {((placements.data?.data as Row[]) ?? []).map((p) => (
                  <tr key={p.id}>
                    <td>{p.stage}</td>
                    <td>{p.entered_at}</td>
                    <td>{p.exited_at ?? "now"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "violations" &&
            (((violations.data?.data as Row[]) ?? []).length === 0 ? (
              <Empty>No violations on this item.</Empty>
            ) : (
              <ul>
                {((violations.data?.data as Row[]) ?? []).map((v) => (
                  <li key={v.id}>
                    {v.severity} · {v.policy} · {v.status}
                    {v.waive_reason ? ` (waived: ${v.waive_reason})` : ""}
                  </li>
                ))}
              </ul>
            ))}
          {tab === "events" && (
            <ul>
              {((events.data?.data as Row[]) ?? []).map((e) => (
                <li key={e.id}>
                  {e.occurred_at?.slice(0, 16)} · {e.event_type}
                </li>
              ))}
            </ul>
          )}
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </>
      )}
    </Drawer>
  );
}
