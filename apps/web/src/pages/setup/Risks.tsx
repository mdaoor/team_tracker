import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w } from "../../api/http";
import { Card } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { CreateForm, Table } from "./common";

export function Risks() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const risks = useList("/risks");
  const projects = useList("/projects");
  const items = useList("/items", { limit: 50 });
  const [cell, setCell] = useState<[number, number] | null>(null);
  const [dragRisk, setDragRisk] = useState<string | null>(null);
  const all = risks.data ?? [];
  const count = (p: number, i: number) =>
    all.filter((r) => r.probability === p && r.impact === i).length;
  const shown = cell ? all.filter((r) => r.probability === cell[0] && r.impact === cell[1]) : all;
  const link = async (itemKey: string) => {
    if (!dragRisk) return;
    try {
      await api.post(w(ws, `/risks/${dragRisk}/links`), { item: itemKey });
      toasts.push(`Linked ${dragRisk} to ${itemKey}; children inherit it`);
      await qc.invalidateQueries({ queryKey: [ws, "/risks"] });
    } catch (e) {
      toasts.error(e);
    }
  };
  return (
    <>
      <Card title="Add risk">
        <CreateForm
          path="/risks"
          refresh={["/risks"]}
          label="Add risk"
          fields={[
            {
              name: "project",
              label: "Project",
              options: (projects.data ?? []).map((p) => p.key),
              required: true,
            },
            { name: "title", label: "Title", required: true },
            { name: "probability", label: "P (1-5)", type: "number" },
            { name: "impact", label: "I (1-5)", type: "number" },
          ]}
        />
      </Card>
      <Card title="Heat map">
        <p className="muted">
          Rows are impact (5 = severe), columns are probability (5 = near certain). Click a cell to list its risks.
          {cell && (
            <>
              {" "}
              <button className="linkbtn" onClick={() => setCell(null)}>
                Show all risks
              </button>
            </>
          )}
        </p>
        <table className="heat" aria-label="Risk heat map">
          <thead>
            <tr>
              <th scope="col">Impact ↓ / Probability →</th>
              {[1, 2, 3, 4, 5].map((p) => (
                <th key={p} scope="col">
                  P{p}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[5, 4, 3, 2, 1].map((i) => (
              <tr key={i}>
                <th scope="row">I{i}</th>
                {[1, 2, 3, 4, 5].map((p) => {
                  const n = count(p, i);
                  return (
                    <td key={p}>
                      <button
                        aria-label={`probability ${p} impact ${i}: ${n}`}
                        aria-pressed={!!cell && cell[0] === p && cell[1] === i}
                        data-count={n}
                        style={{
                          background: n ? `var(--seq${Math.min(6, 1 + n)})` : "var(--surface-2)",
                        }}
                        onClick={() =>
                          setCell(cell && cell[0] === p && cell[1] === i ? null : [p, i])
                        }
                      >
                        {n || ""}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title={cell ? `Risks at P${cell[0]} × I${cell[1]}` : "Register"}>
        <Table
          rows={shown}
          cols={[
            {
              key: "key",
              label: "Key",
              render: (r) => (
                <span draggable onDragStart={() => setDragRisk(r.key)} className="chip">
                  {r.key}
                </span>
              ),
            },
            { key: "title", label: "Title" },
            { key: "status", label: "Status" },
            { key: "score", label: "Score", render: (r) => String(r.probability * r.impact) },
          ]}
        />
      </Card>
      <Card title="Drop a risk on an item to link it">
        <ul className="droplist">
          {(items.data ?? []).slice(0, 12).map((it) => (
            <li key={it.key} onDragOver={(e) => e.preventDefault()} onDrop={() => link(it.key)}>
              {it.key} · {it.title}
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
