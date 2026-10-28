import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import * as itemsApi from "../api/items";
import { api, w } from "../api/http";
import { ago } from "../lib/format";
import type { Json } from "../api/types";
import { patchWithEtag, type Conflict } from "../lib/conflict";
import { Drawer } from "./Drawer";
import { ConflictDialog } from "./ConflictDialog";
import { useToasts } from "./Toasts";
import { Button, Empty, ProvenanceChip } from "./ui";

const PRIORITIES = ["lowest", "low", "medium", "high", "critical"];
type Tab =
  "overview" | "activity" | "discussion" | "evidence" | "placements" | "violations" | "events";

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
    queryFn: () => itemsApi.getItem(ws, itemKey),
  });
  const prov = useQuery({
    queryKey: [ws, "prov", itemKey],
    queryFn: () => itemsApi.itemProvenance(ws, itemKey),
  });
  const placements = useQuery({
    queryKey: [ws, "placements", itemKey],
    queryFn: () => itemsApi.itemPlacements(ws, itemKey),
    enabled: tab === "placements",
  });
  const events = useQuery({
    queryKey: [ws, "events", itemKey],
    queryFn: () => itemsApi.itemEvents(ws, itemKey),
    enabled: tab === "events",
  });
  const it = item.data?.data;
  const people = useQuery({
    queryKey: [ws, "people-handles"],
    queryFn: () => api.get<{ data?: { id: string; handle: string }[] }>(w(ws, "/people")),
    enabled: tab === "discussion",
  });
  const comments = useQuery({
    queryKey: [ws, "comments", itemKey],
    queryFn: () => itemsApi.itemComments(ws, itemKey),
    enabled: tab === "discussion",
  });
  const checklists = useQuery({
    queryKey: [ws, "checklists", itemKey],
    queryFn: () => itemsApi.itemChecklists(ws, itemKey),
    enabled: tab === "discussion",
  });
  const links = useQuery({
    queryKey: [ws, "links", itemKey],
    queryFn: () => itemsApi.itemLinks(ws, itemKey),
    enabled: tab === "discussion",
  });
  const [draft, setDraft] = useState("");
  const handleOf = new Map((people.data?.data ?? []).map((p) => [p.id, p.handle]));
  const post = async () => {
    if (!draft.trim()) return;
    try {
      await itemsApi.addItemComment(ws, itemKey, draft.trim());
      setDraft("");
      await qc.invalidateQueries({ queryKey: [ws, "comments", itemKey] });
    } catch (e) {
      toasts.error(e);
    }
  };
  const violations = useQuery({
    queryKey: [ws, "item-violations", it?.id],
    queryFn: () => itemsApi.itemViolations(ws, it?.id),
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
    toasts.push(`${field} → ${String(value)}`, {
      key: `edit:${field}`,
      undo: () => void save(field, before),
    });
  };

  return (
    <Drawer open onClose={onClose} title={it ? `${it.key} · ${it.title}` : itemKey}>
      {item.isError && <p role="alert">This item does not exist or you may not see it.</p>}
      {it && (
        <>
          <p className="muted">
            reported by {it.reporter ?? "a former member"} · {it.state} · stage {it.stage ?? "—"} ·{" "}
            {it.hours_logged} h logged
          </p>
          <div role="tablist" className="tabs">
            {(
              [
                "overview",
                "activity",
                "discussion",
                "evidence",
                "placements",
                "violations",
                "events",
              ] as Tab[]
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
                      {(vals as Json[]).map((v) => (
                        <span key={v.value}>
                          {v.value}{" "}
                          <ProvenanceChip
                            source={v.source}
                            setBy={v.set_by_name}
                            setAt={v.set_at}
                            inheritedFrom={
                              v.inherited_from
                                ? (v.inherited_from.name ?? v.inherited_from.type)
                                : null
                            }
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
          {tab === "discussion" && (
            <div className="discussion">
              <h3>Comments</h3>
              {(comments.data?.data ?? []).length === 0 ? (
                <Empty>No comments yet.</Empty>
              ) : (
                <ul className="plain">
                  {(comments.data?.data ?? []).map((c) => (
                    <li key={c.id}>
                      <strong>{handleOf.get(c.author_id) ?? "someone"}</strong>{" "}
                      <small className="muted">{ago(c.created_at)}</small>
                      <div>{c.body}</div>
                    </li>
                  ))}
                </ul>
              )}
              <label>
                Add a comment
                <textarea rows={2} value={draft} onChange={(e) => setDraft(e.target.value)} />
              </label>
              <Button onClick={() => void post()} disabled={!draft.trim()}>
                Comment
              </Button>
              <h3>Checklists</h3>
              {(checklists.data?.data ?? []).length === 0 ? (
                <Empty>No checklists.</Empty>
              ) : (
                (checklists.data?.data ?? []).map((cl) => (
                  <section key={cl.id} aria-label={cl.title}>
                    <strong>{cl.title}</strong>{" "}
                    <small className="muted">
                      {cl.done}/{cl.total}
                    </small>
                    <ul className="plain">
                      {cl.items.map((i) => (
                        <li key={i.id}>
                          <span aria-hidden="true">{i.is_done ? "☑" : "☐"}</span>{" "}
                          <span className="sr-only">{i.is_done ? "done: " : "open: "}</span>
                          {i.text}
                        </li>
                      ))}
                    </ul>
                  </section>
                ))
              )}
              <h3>Links</h3>
              {(links.data?.data ?? []).length === 0 ? (
                <Empty>No links.</Empty>
              ) : (
                <ul className="plain">
                  {(links.data?.data ?? []).map((l) => (
                    <li key={l.id}>
                      {l.direction === "out"
                        ? `${l.link_type.replaceAll("_", " ")} → `
                        : `← ${l.link_type.replaceAll("_", " ")} by `}
                      <a href={`?item=${l.other}`}>{l.other}</a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {tab === "evidence" && <p>{it.evidence_count} evidence item(s).</p>}
          {tab === "placements" && (
            <table>
              <tbody>
                {(placements.data?.data ?? []).map((p) => (
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
            ((violations.data?.data ?? []).length === 0 ? (
              <Empty>No violations on this item.</Empty>
            ) : (
              <ul>
                {(violations.data?.data ?? []).map((v) => (
                  <li key={v.id}>
                    {v.severity} · {v.policy} · {v.status}
                    {v.waive_reason ? ` (waived: ${v.waive_reason})` : ""}
                  </li>
                ))}
              </ul>
            ))}
          {tab === "events" && (
            <ul>
              {(events.data?.data ?? []).map((e) => (
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
