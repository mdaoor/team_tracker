import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { queryGantt } from "../../api/items";
import { Empty } from "../../components/ui";
import { layoutGantt, AXIS_H, LABEL_W, ROW_H, type Zoom } from "../../lib/gantt";
import { useWs } from "../../lib/hooks";
import { toFilter, type ViewState } from "../../lib/viewState";

const ROWS: [string, string][] = [
  ["epic", "Epics"],
  ["item", "All items"],
  ["milestone", "Milestones"],
];

/** Time axis, today line, bar labels, milestone diamonds, dependency arrows and day/week/month zoom. Rows default to epics. */
export function GanttLayout({ st }: { st: ViewState }) {
  const ws = useWs();
  const [zoom, setZoom] = useState<Zoom>("week");
  const [rowsKind, setRowsKind] = useState("epic");
  const body = { filter: toFilter(st.chips), baseline: "baseline-1", rows: rowsKind };
  const q = useQuery({
    queryKey: [ws, "query/gantt", body],
    queryFn: () => queryGantt(ws, body),
    enabled: !!ws,
  });
  const controls = (
    <div className="row" role="group" aria-label="Gantt options">
      <label>
        Rows{" "}
        <select value={rowsKind} onChange={(e) => setRowsKind(e.target.value)}>
          {ROWS.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <span role="group" aria-label="Zoom">
        {(["day", "week", "month"] as Zoom[]).map((z) => (
          <button key={z} className="btn" aria-pressed={zoom === z} onClick={() => setZoom(z)}>
            {z}
          </button>
        ))}
      </span>
    </div>
  );
  if (q.isLoading) return <p>Loading…</p>;
  if (q.error) return <p role="alert">{(q.error as Error).message}</p>;
  const model = layoutGantt(
    q.data?.rows ?? [],
    q.data?.dependencies ?? [],
    q.data?.milestones ?? [],
    q.data?.today ?? null,
    zoom,
  );
  if (!model.bars.length)
    return (
      <>
        {controls}
        <Empty>No scheduled {rowsKind === "milestone" ? "milestones" : "items"}.</Empty>
      </>
    );
  return (
    <>
      {controls}
      <div className="vgantt-wrap" role="region" aria-label="Gantt chart" tabIndex={0}>
        <svg
          className="vgantt"
          width={model.width}
          height={model.height}
          role="img"
          aria-label={`Gantt chart, ${model.bars.length} rows`}
        >
          <title>Gantt chart</title>
          {model.ticks.map((t, i) => (
            <g key={i}>
              <line
                x1={t.x}
                x2={t.x}
                y1={AXIS_H - 6}
                y2={model.height}
                stroke="var(--line)"
                strokeWidth={t.major ? 1.5 : 0.5}
              />
              <text x={t.x + 3} y={AXIS_H - 12}>
                {t.label}
              </text>
            </g>
          ))}
          {model.todayX != null && (
            <g data-today>
              <line
                x1={model.todayX}
                x2={model.todayX}
                y1={AXIS_H - 6}
                y2={model.height}
                stroke="var(--crit)"
                strokeWidth={1.5}
              />
              <text x={model.todayX + 3} y={10} fill="var(--crit)">
                today
              </text>
            </g>
          )}
          {model.bars.map((b) => (
            <g key={b.key} data-row={b.key}>
              <text x={4} y={b.y + ROW_H / 2 + 4} className="gl">
                {b.key.length > 22 ? b.key.slice(0, 21) + "…" : b.key}
              </text>
              {b.ghost && (
                <rect
                  data-ghost
                  x={b.ghost.x}
                  y={b.y + 3}
                  width={b.ghost.w}
                  height={4}
                  fill="var(--text-3)"
                  opacity={0.5}
                />
              )}
              <rect
                x={b.x}
                y={b.y + 8}
                width={b.w}
                height={b.rollup ? 8 : 12}
                rx={3}
                fill={b.done ? "var(--s5)" : b.slip ? "var(--serious)" : "var(--s1)"}
              >
                <title>{`${b.key} ${b.title}`}</title>
              </rect>
              {b.w > 60 && (
                <text x={b.x + 4} y={b.y + 18} fill="#fff" fontSize={9}>
                  {b.title.slice(0, Math.floor(b.w / 6))}
                </text>
              )}
            </g>
          ))}
          {model.arrows.map((a, i) => (
            <path
              key={i}
              d={a.d}
              fill="none"
              stroke="var(--text-3)"
              strokeWidth={1}
              markerEnd="url(#arrow)"
              data-dep={`${a.from}>${a.to}`}
            />
          ))}
          <defs>
            <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 z" fill="var(--text-3)" />
            </marker>
          </defs>
          {model.milestones.map((m, i) => (
            <g key={i} data-milestone={m.name}>
              <path d={`M${m.x},${AXIS_H - 4} l5,5 l-5,5 l-5,-5 z`} fill="var(--s4)">
                <title>{`${m.project} · ${m.name}`}</title>
              </path>
            </g>
          ))}
          <line x1={LABEL_W} x2={LABEL_W} y1={AXIS_H - 6} y2={model.height} stroke="var(--line)" />
        </svg>
      </div>
    </>
  );
}
