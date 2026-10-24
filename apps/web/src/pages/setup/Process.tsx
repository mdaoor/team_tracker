import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../../api/http";
import { Card, Chip } from "../../components/ui";
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
            <Chip>
              {s.name}
              <small>
                {" "}
                {counts[s.key] ?? 0} items · {s.expected_duration_days ?? "–"} d · WIP{" "}
                {s.wip_limit ?? "∞"} · {s.maps_to_state_category}
                {s.requires_approval ? " · approval" : ""}
              </small>
            </Chip>
            <button aria-label={`Move ${s.name} left`} onClick={() => move(s.key, -1)}>
              ←
            </button>
            <button aria-label={`Move ${s.name} right`} onClick={() => move(s.key, 1)}>
              →
            </button>
          </li>
        ))}
      </ol>
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
