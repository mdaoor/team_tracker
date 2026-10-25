import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { queryGantt } from "../../api/items";
import { Empty } from "../../components/ui";
import { ganttSvg, layoutGantt, type Zoom } from "@tt/presentation/gantt";
import "@tt/presentation/gantt.css";
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
  // The shared module (apps/presentation) draws; only rows with dates are charted, as before.
  const model = layoutGantt({
    rows: (q.data?.rows ?? []).filter((r) => r.planned_start && r.planned_end),
    deps: q.data?.dependencies ?? [],
    milestones: q.data?.milestones ?? [],
    today: q.data?.today ?? null,
    zoom,
    labelWidth: 170,
    axisHeight: 34,
  });
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
      <div
        className="vgantt-wrap roadmap"
        role="region"
        aria-label="Gantt chart"
        tabIndex={0}
        // ganttSvg escapes every interpolated value (apps/presentation/src/gantt.js)
        dangerouslySetInnerHTML={{
          __html: ganttSvg(model, { label: `Gantt chart, ${model.bars.length} rows` }),
        }}
      />
    </>
  );
}
