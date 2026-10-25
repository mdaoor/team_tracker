import { DndContext, useDraggable, useDroppable, type DragEndEvent } from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../../api/http";
import { Empty } from "../../components/ui";
import { useWs } from "../../lib/hooks";
import { chipId, toFilter, type Chip, type ViewState } from "../../lib/viewState";
import { Card } from "./Card";

type Drill = (label: string, chips: Chip[]) => void;

function useItems(st: ViewState) {
  const ws = useWs();
  const body = {
    filter: toFilter(st.chips),
    sort: [{ field: st.sort.split(":")[0], dir: st.sort.split(":")[1] ?? "desc" }],
    group_by: st.groupBy ? [st.groupBy] : [],
    weight_scheme_id: st.scheme ?? undefined,
    page: { size: 200 },
  };
  return useQuery({
    queryKey: [ws, "query/items", body],
    queryFn: () => api.post<Row>(w(ws, "/query/items"), body),
    enabled: !!ws,
  });
}

export function ListLayout({ st }: { st: ViewState }) {
  const q = useItems(st);
  if (q.isLoading) return <p>Loading…</p>;
  if (q.error) return <p role="alert">{(q.error as Error).message}</p>;
  const items: Row[] = q.data?.items ?? [];
  if (!items.length) return <Empty>No items match these filters.</Empty>;
  const groups = st.groupBy
    ? [...new Set(items.map((i) => (i.dims?.[st.groupBy!] ?? [])[0] ?? i[st.groupBy!] ?? "—"))]
    : [null];
  return (
    <div className="vlist">
      <p aria-live="polite">{q.data?.page.total} items</p>
      {groups.map((g) => (
        <section key={String(g)}>
          {g !== null && <h3>{String(g)}</h3>}
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Title</th>
                <th>Stage</th>
                <th>State</th>
                <th>Priority</th>
                <th>Weight</th>
                <th>Assignee</th>
              </tr>
            </thead>
            <tbody>
              {items
                .filter(
                  (i) =>
                    g === null ||
                    (i.dims?.[st.groupBy!] ?? [])[0] === g ||
                    i[st.groupBy!] === g ||
                    (g === "—" && !(i.dims?.[st.groupBy!] ?? []).length && !i[st.groupBy!]),
                )
                .map((i) => (
                  <tr key={i.key}>
                    <td>
                      <Card item={i} />
                    </td>
                    <td>{i.title}</td>
                    <td>{i.stage}</td>
                    <td>{i.state}</td>
                    <td>{i.priority}</td>
                    <td>{i.weight}</td>
                    <td>{i.assignee}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

/** The drag handle is its own button, a sibling of the card's link (axe `nested-interactive`; keyboard drag works from it). */
function DragCard({ item }: { item: Row }) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform } = useDraggable({
    id: item.key,
  });
  return (
    <div
      ref={setNodeRef}
      className="dragcard"
      style={{ transform: transform ? `translate(${transform.x}px,${transform.y}px)` : undefined }}
    >
      <button
        ref={setActivatorNodeRef}
        className="drag-handle"
        aria-label={`Drag ${item.key}`}
        {...attributes}
        {...listeners}
      >
        ⠿
      </button>
      <Card item={item} />
    </div>
  );
}
function Col({
  id,
  over,
  children,
  title,
  count,
  onCount,
}: {
  id: string;
  over?: boolean;
  title: string;
  count?: number;
  onCount?: () => void;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <section
      ref={setNodeRef}
      className={`vcol${over ? " over" : ""}${isOver ? " drop" : ""}`}
      aria-label={title}
      data-col={id}
    >
      <h3>
        {title}
        {count != null && onCount && (
          <>
            {" · "}
            <button className="linkbtn" onClick={onCount} aria-label={`Show the ${count} items in ${title}`}>
              {count}
            </button>
          </>
        )}
      </h3>
      {children}
    </section>
  );
}

/** Board (project) and Pipeline (process) share the drag mechanics: a drop is a state change or a stage placement. */
export function Columns({
  kind,
  ref_,
  st,
  drill,
}: {
  kind: "board" | "pipeline";
  ref_: string;
  st: ViewState;
  drill?: Drill;
}) {
  const ws = useWs();
  const qc = useQueryClient();
  const path = kind === "board" ? `/projects/${ref_}/board` : `/processes/${ref_}/pipeline`;
  const q = useQuery({
    queryKey: [ws, path, st.chips],
    queryFn: () => api.get<Row>(w(ws, path), { filter: JSON.stringify(toFilter(st.chips)) }),
    enabled: !!ws && !!ref_,
  });
  const [err, setErr] = useState<string | null>(null);
  const move = useMutation({
    mutationFn: ({ key, to }: { key: string; to: string }) =>
      kind === "board"
        ? api.post(w(ws, `/items/${key}/state`), { state: to })
        : api.put(w(ws, `/items/${key}/stage`), { process: ref_, stage: to }),
    onSuccess: () => {
      setErr(null);
      void qc.invalidateQueries({ queryKey: [ws, path] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  const onEnd = (e: DragEndEvent) => {
    if (e.over && e.active) move.mutate({ key: String(e.active.id), to: String(e.over.id) });
  };
  if (!ref_) return <Empty>Pick a {kind === "board" ? "project" : "process"} first.</Empty>;
  if (q.isLoading) return <p>Loading…</p>;
  if (q.error) return <p role="alert">{(q.error as Error).message}</p>;
  const cols: Row[] = q.data?.columns ?? [];
  const idOf = (c: Row) => (kind === "board" ? c.state : c.stage);
  return (
    <>
      {err && <p role="alert">{err}</p>}
      <DndContext onDragEnd={onEnd}>
        <div className="vcols">
          {cols.map((c) => (
            <Col
              key={idOf(c)}
              id={idOf(c)}
              over={c.over_wip}
              title={c.name}
              count={kind === "board" ? c.count : c.wip_count}
              onCount={
                drill
                  ? () =>
                      drill(`${c.name}`, [
                        {
                          id: chipId(),
                          label: kind === "board" ? `state = ${c.name}` : `stage = ${c.name}`,
                          clause:
                            kind === "board"
                              ? { state: { in: [c.state] } }
                              : { stage: { in: [c.stage] } },
                        },
                      ])
                  : undefined
              }
            >
              {c.wip_limit ? <small>limit {c.wip_limit}</small> : null}
              {kind === "pipeline" && (
                <small>
                  Σ weight {c.weight_sum}
                  {c.over_expected ? ` · ${c.over_expected} over expected` : ""}
                </small>
              )}
              {c.items.map((i: Row) => (
                <DragCard key={i.key} item={i} />
              ))}
            </Col>
          ))}
        </div>
      </DndContext>
    </>
  );
}

export function MatrixLayout({ st, drill }: { st: ViewState; drill: Drill }) {
  const ws = useWs();
  const body = {
    filter: toFilter(st.chips),
    rows: st.matrix.rows,
    cols: st.matrix.cols,
    measure: st.matrix.measure,
    weight_scheme_id: st.scheme ?? undefined,
  };
  const q = useQuery({
    queryKey: [ws, "query/aggregate", body],
    queryFn: () => api.post<Row>(w(ws, "/query/aggregate"), body),
    enabled: !!ws,
  });
  if (q.isLoading) return <p>Loading…</p>;
  if (q.error) return <p role="alert">{(q.error as Error).message}</p>;
  const d = q.data!;
  const max = Math.max(1, ...d.cells.flat());
  const dimChip = (dim: string, v: string): Chip => ({
    id: chipId(),
    label: `${dim} = ${v}`,
    clause: ["stage", "project"].includes(dim)
      ? { [dim]: { in: [v] } }
      : dim === "state"
        ? { state: { category: [v] } }
        : dim === "priority"
          ? { priority: { in: [v] } }
          : { dims: { [dim]: { in: [v] } } },
  });
  return (
    <div className="vmatrix">
      <table>
        <thead>
          <tr>
            <th>
              {st.matrix.rows} \ {st.matrix.cols}
            </th>
            {d.cols.map((c: string) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {d.rows.map((r: Row, ri: number) => (
            <tr key={r.value}>
              <th>{r.value}</th>
              {d.cols.map((c: string, ci: number) => {
                const v = d.cells[ri][ci];
                const level = v ? Math.min(6, 1 + Math.floor((v / max) * 5.999)) : 0;
                return (
                  <td
                    key={c}
                    className="cell"
                    data-cell={`${r.value}|${c}`}
                    style={{
                      background: level ? `var(--seq${level})` : undefined,
                      color: level ? `var(--seq${level}-fg)` : undefined,
                    }}
                    onClick={() =>
                      v &&
                      drill(`${r.value} × ${c}`, [
                        dimChip(st.matrix.rows, r.value),
                        dimChip(st.matrix.cols, c),
                      ])
                    }
                  >
                    {v || ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Month grid with titles and previous/next/today navigation (UX review: calendar showed bare keys and had no month nav). */
export function CalendarLayout({ st }: { st: ViewState }) {
  const ws = useWs();
  const [offset, setOffset] = useState(0);
  const now = new Date();
  const anchor = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offset, 1));
  const from = anchor;
  const to = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + 1, 0));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const body = {
    filter: toFilter(st.chips),
    from: iso(from),
    to: iso(to),
    fields: ["due_date", "planned_end", "milestones", "iterations"],
  };
  const q = useQuery({
    queryKey: [ws, "query/calendar", body],
    queryFn: () => api.post<Row>(w(ws, "/query/calendar"), body),
    enabled: !!ws,
  });
  const label = anchor.toLocaleString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const nav = (
    <div className="row" role="group" aria-label="Month">
      <button className="btn" aria-label="Previous month" onClick={() => setOffset((o) => o - 1)}>
        ‹
      </button>
      <strong aria-live="polite">{label}</strong>
      <button className="btn" aria-label="Next month" onClick={() => setOffset((o) => o + 1)}>
        ›
      </button>
      <button className="btn btn-ghost" onClick={() => setOffset(0)} disabled={offset === 0}>
        Today
      </button>
    </div>
  );
  if (q.isLoading) return <p>Loading…</p>;
  if (q.error) return <p role="alert">{(q.error as Error).message}</p>;
  const days: Record<string, Row[]> = q.data?.days ?? {};
  const cells: (string | null)[] = [
    ...Array((from.getUTCDay() + 6) % 7).fill(null),
    ...Array.from({ length: to.getUTCDate() }, (_, i) =>
      iso(new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), i + 1))),
    ),
  ];
  return (
    <div>
      {nav}
      <div className="vcal" role="group" aria-label={label}>
        {WEEKDAYS.map((d) => (
          <div key={d} className="vcal-h" aria-hidden>
            {d}
          </div>
        ))}
        {cells.map((d, i) => (
          <div key={i} data-day={d ?? ""} className={d === iso(now) ? "today" : ""}>
            {d && <strong>{d.slice(8)}</strong>}
            {(d ? (days[d] ?? []) : []).map((e, j) => (
              <div key={j} className={`ev ${e.kind}`} title={`${e.key ?? e.kind}: ${e.title ?? ""}`}>
                {e.key && <span className="key">{e.key} </span>}
                {e.title}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function NowLayout() {
  const ws = useWs();
  const q = useQuery({
    queryKey: [ws, "now"],
    queryFn: () => api.get<Row>(w(ws, "/now")),
    enabled: !!ws,
    refetchInterval: 30000,
  });
  const rows: Row[] = q.data?.data ?? [];
  if (!rows.length) return <Empty>Nobody is working on anything right now.</Empty>;
  return (
    <ul>
      {rows.map((r) => (
        <li key={r.id}>
          <strong>{r.person}</strong> → {r.item_key} {r.item_title}{" "}
          <small>({r.since_minutes} min)</small>
        </li>
      ))}
    </ul>
  );
}
