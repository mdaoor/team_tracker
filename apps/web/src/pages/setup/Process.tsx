import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../../api/http";
import { Card } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useList, useObj, useWs } from "../../lib/hooks";
import { CreateForm } from "./common";

const seq = (n: number, max: number) =>
  `var(--seq${Math.min(6, 1 + Math.floor((n / Math.max(1, max)) * 5))})`;

export function Process() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const processes = useList("/processes");
  const [drag, setDrag] = useState<string | null>(null);
  const proc = processes.data?.[0];
  const board = useObj(`/processes/${proc?.key}/board`, undefined, !!proc);
  const counts: Record<string, number> = {};
  for (const col of (board.data?.columns ?? []) as Row[])
    counts[col.stage] = col.items?.length ?? col.wip ?? 0;
  const max = Math.max(1, ...Object.values(counts));

  const reorder = async (order: string[]) => {
    try {
      await api.patch(w(ws, `/processes/${proc?.key}/stages:reorder`), { order });
      await qc.invalidateQueries({ queryKey: [ws, "/processes"] });
    } catch (e) {
      toasts.error(e);
    }
  };
  const stages: Row[] = proc?.stages ?? [];
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Row>({});
  const open = (s: Row) => {
    setEditing(s.key);
    setDraft({
      name: s.name,
      expected_duration_days: s.expected_duration_days ?? "",
      wip_limit: s.wip_limit ?? "",
      requires_approval: !!s.requires_approval,
      category: s.category,
      maps_to_state_category: s.maps_to_state_category,
    });
  };
  const save = async () => {
    try {
      const num = (v: unknown) => (v === "" || v == null ? null : Number(v));
      await api.patch(w(ws, `/processes/${proc?.key}/stages/${editing}`), {
        ...draft,
        expected_duration_days: num(draft.expected_duration_days),
        wip_limit: num(draft.wip_limit),
      });
      toasts.push("Stage updated");
      setEditing(null);
      await qc.invalidateQueries({ queryKey: [ws, "/processes"] });
    } catch (e) {
      toasts.error(e);
    }
  };
  // live mini-pipeline: what the line looks like with the edits applied (bar width = expected days)
  const preview = stages.map((s) =>
    s.key === editing
      ? { ...s, expected_duration_days: draft.expected_duration_days === "" ? null : Number(draft.expected_duration_days), name: draft.name, wip_limit: draft.wip_limit === "" ? null : Number(draft.wip_limit) }
      : s,
  );
  const totalDays = Math.max(1, preview.reduce((n, s) => n + (Number(s.expected_duration_days) || 1), 0));
  const drop = (target: string) => {
    if (!drag || drag === target) return;
    const keys = stages.map((s) => s.key).filter((k) => k !== drag);
    keys.splice(keys.indexOf(target), 0, drag);
    void reorder(keys);
  };
  const move = (key: string, d: number) => {
    const keys = stages.map((s) => s.key);
    const i = keys.indexOf(key);
    const j = i + d;
    if (j < 0 || j >= keys.length) return;
    [keys[i], keys[j]] = [keys[j], keys[i]];
    void reorder(keys);
  };
  return (
    <Card title={`Process · ${proc?.name ?? ""}`}>
      <ol className="stages" aria-label="Stages">
        {stages.map((s) => (
          <li
            key={s.key}
            draggable
            onDragStart={() => setDrag(s.key)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => drop(s.key)}
            data-stage={s.key}
            style={{ background: seq(counts[s.key] ?? 0, max) }}
          >
            <button
              className="chip stage-chip"
              aria-haspopup="dialog"
              aria-expanded={editing === s.key}
              onClick={() => (editing === s.key ? setEditing(null) : open(s))}
            >
              {s.name}
              <small>
                {" "}
                {counts[s.key] ?? 0} items · {s.expected_duration_days ?? "–"} d · WIP{" "}
                {s.wip_limit ?? "∞"} · {s.maps_to_state_category}
                {s.requires_approval ? " · approval" : ""}
              </small>
            </button>
            <button aria-label={`Move ${s.name} left`} onClick={() => move(s.key, -1)}>
              ←
            </button>
            <button aria-label={`Move ${s.name} right`} onClick={() => move(s.key, 1)}>
              →
            </button>
          </li>
        ))}
      </ol>
      {editing && (
        <div role="dialog" aria-label={`Edit stage ${editing}`} className="card stage-edit">
          <div className="row">
            <label>
              Name
              <input value={String(draft.name ?? "")} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <label>
              Expected days
              <input
                type="number"
                min={0}
                value={String(draft.expected_duration_days ?? "")}
                onChange={(e) => setDraft({ ...draft, expected_duration_days: e.target.value })}
              />
            </label>
            <label>
              WIP limit
              <input
                type="number"
                min={0}
                value={String(draft.wip_limit ?? "")}
                onChange={(e) => setDraft({ ...draft, wip_limit: e.target.value })}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={!!draft.requires_approval}
                onChange={(e) => setDraft({ ...draft, requires_approval: e.target.checked })}
              />{" "}
              Needs approval
            </label>
            <label>
              Category
              <select value={String(draft.category ?? "")} onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
                {["intake", "build", "verify", "release", "done"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="mini-pipeline" role="img" aria-label="Mini pipeline preview">
            {preview.map((s) => (
              <span
                key={s.key}
                className={s.key === editing ? "on" : ""}
                style={{ flexGrow: Math.max(1, Number(s.expected_duration_days) || 1) / totalDays }}
                title={`${s.name}: ${s.expected_duration_days ?? "–"} d`}
              >
                {s.name}
              </span>
            ))}
          </div>
          <div className="row">
            <button className="btn btn-primary" onClick={() => void save()}>
              Save stage
            </button>
            <button className="btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {proc && (
        <CreateForm
          path={`/processes/${proc.key}/stages`}
          refresh={["/processes"]}
          label="Add stage"
          fields={[
            { name: "key", label: "Key", required: true },
            { name: "name", label: "Name", required: true },
            {
              name: "category",
              label: "Category",
              options: ["intake", "build", "verify", "release", "done"],
              required: true,
            },
            {
              name: "maps_to_state_category",
              label: "Maps to state",
              options: ["open", "active", "done"],
              required: true,
            },
            { name: "expected_duration_days", label: "Expected days", type: "number" },
            { name: "wip_limit", label: "WIP limit", type: "number" },
          ]}
        />
      )}
    </Card>
  );
}
