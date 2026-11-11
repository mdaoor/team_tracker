import { AddToPresentation } from "../../components/AddToPresentation";
import { useMemo, useState } from "react";
import type { Project, Team, WeightScheme } from "../../api/types";
import { Empty } from "../../components/ui";
import { METRICS, seriesOf } from "../../lib/governance";
import { useList, useWs } from "../../lib/hooks";

const iso = (d: Date) => d.toISOString().slice(0, 10);
const shift = (d: Date, days: number) => new Date(d.getTime() + days * 86_400_000);

function LineChart({
  cur,
  prev,
  label,
}: {
  cur: { date: string; v: number | null }[];
  prev?: { v: number | null }[];
  label: string;
}) {
  const W = 560,
    H = 160,
    P = 24;
  const vals = [...cur, ...(prev ?? [])].map((p) => p.v).filter((v): v is number => v != null);
  if (!vals.length) return <Empty>No data for this period.</Empty>;
  const max = Math.max(...vals, 1e-9),
    min = Math.min(0, ...vals);
  const x = (i: number, n: number) => P + (n <= 1 ? 0 : (i / (n - 1)) * (W - 2 * P));
  const y = (v: number) => H - P - ((v - min) / (max - min || 1)) * (H - 2 * P);
  const path = (s: { v: number | null }[]) =>
    s
      .map((p, i) =>
        p.v == null
          ? ""
          : `${i && s[i - 1].v != null ? "L" : "M"}${x(i, s.length).toFixed(1)},${y(p.v).toFixed(1)}`,
      )
      .join(" ");
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`${label}, ${cur.length} days, max ${max}`}
      className="chart"
    >
      {prev && <path d={path(prev)} fill="none" stroke="var(--text-3)" strokeDasharray="4 3" />}
      <path d={path(cur)} fill="none" stroke="var(--s1)" strokeWidth={2} />
      <text x={P} y={12} fontSize={10} fill="var(--text-2)">
        {max.toFixed(max < 10 ? 2 : 0)}
      </text>
      <text x={P} y={H - 6} fontSize={10} fill="var(--text-2)">
        {cur[0]?.date}
      </text>
      <text x={W - P} y={H - 6} fontSize={10} textAnchor="end" fill="var(--text-2)">
        {cur[cur.length - 1]?.date}
      </text>
    </svg>
  );
}

export function Performance() {
  const ws = useWs();
  const projects = useList<Project>("/projects");
  const teams = useList<Team>("/teams");
  const schemes = useList<WeightScheme>("/weight-schemes");
  const [scope, setScope] = useState("workspace:" + ws);
  const [days, setDays] = useState(30);
  const [scheme, setScheme] = useState("");
  const [compare, setCompare] = useState(true);
  const [asTable, setAsTable] = useState(false);
  const [type, id] = scope.split(/:(.*)/s);
  const today = new Date();
  const q = (offset: number) => ({
    scope_type: type,
    scope_id: id,
    from: iso(shift(today, -days - offset)),
    to: iso(shift(today, -offset)),
    ...(scheme ? { scheme } : {}),
  });
  const cur = useList("/metric-snapshots", q(0));
  const prev = useList("/metric-snapshots", q(days), compare);
  const rows = useMemo(() => cur.data ?? [], [cur.data]);
  const charts = useMemo(
    () =>
      METRICS.map((m) => ({
        m,
        cur: seriesOf(rows, m.key),
        prev: compare ? seriesOf(prev.data ?? [], m.key) : undefined,
      })).filter((c) => c.cur.some((p) => p.v != null)),
    [rows, prev.data, compare],
  );
  const csv = () => {
    const head = ["date", ...charts.map((c) => c.m.key)];
    const lines = rows.map((r, i) => [r.date, ...charts.map((c) => c.cur[i]?.v ?? "")].join(","));
    const blob = new Blob(
      [
        `# Exported ${new Date().toISOString()} scope ${scope}\n${head.join(",")}\n${lines.join("\n")}`,
      ],
      { type: "text/csv" },
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "team-performance.csv";
    a.click();
  };
  return (
    <div className="gov">
      <div className="row">
        <h1>Team performance</h1>
        <AddToPresentation
          build={() => ({ kind: "kpis", title: "Team performance", config: {} })}
        />
        <span className="grow" />
        <select aria-label="Scope" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value={"workspace:" + ws}>Workspace</option>
          {(teams.data ?? []).map((t: Team) => (
            <option key={t.id} value={`team:${t.slug}`}>
              Team · {t.name}
            </option>
          ))}
          {(projects.data ?? []).map((p: Project) => (
            <option key={p.id} value={`project:${p.key}`}>
              Project · {p.key}
            </option>
          ))}
        </select>
        <select
          aria-label="Weight scheme"
          value={scheme}
          onChange={(e) => setScheme(e.target.value)}
        >
          <option value="">Default scheme</option>
          {(schemes.data ?? []).map((s: WeightScheme) => (
            <option key={s.id} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30, 90].map((d) => (
            <option key={d} value={d}>
              {d} days
            </option>
          ))}
        </select>
        <label>
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />{" "}
          Compare with previous
        </label>
        <label>
          <input type="checkbox" checked={asTable} onChange={(e) => setAsTable(e.target.checked)} />{" "}
          Table
        </label>
        <button className="btn" onClick={csv} disabled={!rows.length}>
          CSV
        </button>
      </div>
      <p>
        <small>Weight scheme in use: {scheme || "default"}</small>
      </p>
      {charts.length === 0 ? (
        <Empty>No metric snapshots in this period. They are written nightly.</Empty>
      ) : asTable ? (
        <table aria-label="Metrics table">
          <thead>
            <tr>
              <th>Date</th>
              {charts.map((c) => (
                <th key={c.m.key}>{c.m.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.date}>
                <td>{r.date}</td>
                {charts.map((c) => (
                  <td key={c.m.key}>{c.cur[i]?.v ?? "—"}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="charts">
          {charts.map((c) => (
            <figure key={c.m.key}>
              <figcaption>{c.m.label}</figcaption>
              <LineChart cur={c.cur} prev={c.prev} label={c.m.label} />
            </figure>
          ))}
        </div>
      )}
    </div>
  );
}
