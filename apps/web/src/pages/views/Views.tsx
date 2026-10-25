import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, w, type Row } from "../../api/http";
import { AddToPresentation } from "../../components/AddToPresentation";
import { useToasts } from "../../components/Toasts";
import { Button } from "../../components/ui";
import { downloadPost } from "../../lib/download";
import { useList, useWs } from "../../lib/hooks";
import { slideFromView } from "../../lib/slides";
import {
  LAYOUTS,
  PRESETS,
  sortParam,
  toFilter,
  useDrillPath,
  useViewState,
  type Layout,
} from "../../lib/viewState";
import { FilterBar } from "./Filters";
import {
  CalendarLayout,
  Columns,
  GanttLayout,
  ListLayout,
  MatrixLayout,
  NowLayout,
} from "./Layouts";

const SORTS = [
  "priority:desc",
  "weight:desc",
  "due_date:asc",
  "key:asc",
  "stage_age:desc",
  "last_touched:desc",
];
const GROUPS = ["", "project", "stage", "state", "priority", "assignee", "team", "type", "tags"];
const DIMS = ["stage", "state", "project", "priority", "assignee", "team", "type", "tags"];

export function ViewsPage() {
  const ws = useWs();
  const qc = useQueryClient();
  const [st, set] = useViewState();
  const { push, pop } = useDrillPath(st, set);
  const schemes = useList("/weight-schemes");
  const projects = useList("/projects");
  const processes = useList("/processes");
  const views = useList("/views");
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (
        e.key === "Backspace" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName ?? "") &&
        st.drill.length
      )
        pop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const toasts = useToasts();
  useEffect(() => {
    if (st.ignored) toasts.push("The filter in this link could not be read, so it was ignored.", { key: "filter-ignored" });
  }, [st.ignored, toasts]);
  const save = useMutation({
    mutationFn: (shared: boolean) =>
      api.post(w(ws, "/views"), {
        name,
        filter: toFilter(st.chips),
        group_by: st.groupBy ? [st.groupBy] : [],
        sort_by: sortParam(st.sort),
        layout: ["list", "board", "pipeline", "matrix", "gantt", "calendar"].includes(st.layout)
          ? st.layout
          : "list",
        weight_scheme_id: st.scheme,
        is_shared: shared,
      }),
    onSuccess: () => {
      setName("");
      void qc.invalidateQueries({ queryKey: [ws, "/views"] });
    },
  });
  const exportCsv = () =>
    downloadPost(
      toasts,
      w(ws, "/query/items:csv"),
      { filter: toFilter(st.chips), sort: sortParam(st.sort) },
      "items.csv",
      "CSV",
    );
  const open = (v: Row) =>
    set({
      chips: [{ id: `v${v.id}`, label: `view: ${v.name}`, clause: v.filter }],
      layout: (v.layout as Layout) ?? "list",
      scheme: v.weight_scheme_id ?? null,
      groupBy: v.group_by?.[0] ?? null,
    });

  const needsTarget = st.layout === "board" || st.layout === "pipeline";
  const targetList = st.layout === "board" ? projects.data : processes.data;
  const ref = target || targetList?.[0]?.key || "";
  return (
    <div className="views">
      <h1 className="sr-only">Views</h1>
      <div className="vtoolbar" role="toolbar" aria-label="View options">
        <div className="vtabs" role="tablist">
          {LAYOUTS.map((l) => (
            <Button
              key={l}
              role="tab"
              aria-selected={st.layout === l}
              onClick={() => set({ layout: l })}
            >
              {l}
            </Button>
          ))}
        </div>
        <label>
          Group{" "}
          <select
            value={st.groupBy ?? ""}
            onChange={(e) => set({ groupBy: e.target.value || null })}
          >
            {GROUPS.map((g) => (
              <option key={g} value={g}>
                {g || "none"}
              </option>
            ))}
          </select>
        </label>
        <label>
          Sort{" "}
          <select value={st.sort} onChange={(e) => set({ sort: e.target.value })}>
            {SORTS.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          Weights{" "}
          <select value={st.scheme ?? ""} onChange={(e) => set({ scheme: e.target.value || null })}>
            <option value="">default</option>
            {(schemes.data ?? [])
              .filter((s) => s.name !== "default") // the empty option above *is* the default scheme (UX review, "also")
              .map((s) => (
              <option key={s.id} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {needsTarget && (
          <label>
            {st.layout === "board" ? "Project" : "Process"}{" "}
            <select value={ref} onChange={(e) => setTarget(e.target.value)}>
              {(targetList ?? []).map((t) => (
                <option key={t.key}>{t.key}</option>
              ))}
            </select>
          </label>
        )}
        {st.layout === "matrix" && (
          <>
            <label>
              Rows{" "}
              <select
                value={st.matrix.rows}
                onChange={(e) => set({ matrix: { ...st.matrix, rows: e.target.value } })}
              >
                {DIMS.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
            <label>
              Cols{" "}
              <select
                value={st.matrix.cols}
                onChange={(e) => set({ matrix: { ...st.matrix, cols: e.target.value } })}
              >
                {DIMS.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
            <label>
              Measure{" "}
              <select
                value={st.matrix.measure}
                onChange={(e) => set({ matrix: { ...st.matrix, measure: e.target.value } })}
              >
                {["count", "weight", "hours", "estimate"].map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
          </>
        )}
        <Button onClick={() => void exportCsv()}>Export CSV</Button>
        <AddToPresentation build={() => slideFromView(st)} />
      </div>
      {st.chips.length === 0 && (
        <div className="row presets" role="group" aria-label="Quick views">
          {PRESETS.map((p) => (
            <Button key={p.id} onClick={() => set({ chips: [p.chip()], layout: p.layout })}>
              {p.label}
            </Button>
          ))}
        </div>
      )}
      <FilterBar
        chips={st.chips}
        onChange={(chips) =>
          set({
            chips,
            drill: st.drill.filter((d) => d.chips.every((c) => chips.some((x) => x.id === c.id))),
          })
        }
      />
      {st.drill.length > 0 && (
        <nav className="crumbs" aria-label="Drill-down">
          <span>All</span>
          {st.drill.map((d, i) => (
            <span key={i}>› {d.label}</span>
          ))}
          <Button variant="ghost" onClick={pop}>
            Back ⌫
          </Button>
        </nav>
      )}
      <div className="row">
        <input
          aria-label="View name"
          placeholder="Save view as…"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button disabled={!name} onClick={() => save.mutate(false)}>
          Save
        </Button>
        <Button disabled={!name} onClick={() => save.mutate(true)}>
          Save shared
        </Button>
        <select
          aria-label="Saved views"
          value=""
          onChange={(e) => {
            const v = (views.data ?? []).find((x) => x.id === e.target.value);
            if (v) open(v);
          }}
        >
          <option value="">saved views…</option>
          {(views.data ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
              {v.is_shared ? " (shared)" : ""}
            </option>
          ))}
        </select>
      </div>
      {st.layout === "list" && <ListLayout st={st} />}
      {(st.layout === "board" || st.layout === "pipeline") && (
        <Columns kind={st.layout} ref_={ref} st={st} drill={push} />
      )}
      {st.layout === "matrix" && <MatrixLayout st={st} drill={push} />}
      {st.layout === "gantt" && <GanttLayout st={st} />}
      {st.layout === "calendar" && <CalendarLayout st={st} />}
      {st.layout === "now" && <NowLayout />}
    </div>
  );
}
