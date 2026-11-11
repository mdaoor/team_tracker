import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { patchItemFields } from "../../api/items";
import * as presentApi from "../../api/present";
import { approveBaseline } from "../../api/projects";
import type { Plan, PlanBarRow, Project } from "../../api/types";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty } from "../../components/ui";
import { useList, useMe, useObj, useWs } from "../../lib/hooks";
import { HEALTH_LABEL, STATUS_LABEL, axisOf, barOf, dateEditError } from "../../lib/plan";

export function PlanRoute() {
  const key = useParams()["*"]?.split("/")[0];
  return key ? <PlanPage projectKey={key} /> : <PlanIndex />;
}

function PlanIndex() {
  const projects = useList<Project>("/projects");
  return (
    <div className="plan">
      <h1>Project plans</h1>
      {(projects.data ?? []).length === 0 ? (
        <Empty>No projects yet.</Empty>
      ) : (
        <ul className="plain" aria-label="Projects">
          {(projects.data ?? []).map((p) => (
            <li key={p.key}>
              <Link to={p.key}>
                {p.key} · {p.name}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The roadmap editor: phases and deliverables as bars on one axis, dates editable in place, "Present this plan" in one click. */
function PlanPage({ projectKey }: { projectKey: string }) {
  const ws = useWs();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can, admin } = useMe();
  const plan = useObj<Plan>(`/projects/${projectKey}/plan`);
  const [error, setError] = useState<{ key: string; msg: string } | null>(null);
  const refresh = () =>
    void qc.invalidateQueries({ queryKey: [ws, `/projects/${projectKey}/plan`] });
  const data = plan.data;
  const rows: PlanBarRow[] = (data?.phases ?? []).flatMap((p) => [
    { ...p, level: 0 },
    ...(p.deliverables ?? []).map((d) => ({ ...d, level: 1, phase: p.key })),
  ]);
  const axis = axisOf(rows);
  const canWrite = admin || !!can["project.write"] || !!can["item.write_overlay"];

  const edit = useMutation({
    mutationFn: async ({
      key,
      field,
      value,
    }: {
      key: string;
      field: "planned_start" | "planned_end";
      value: string;
    }) => {
      return patchItemFields(ws, key, { [field]: value || null });
    },
    onSuccess: refresh,
    onError: (e) => toasts.error(e),
  });
  const present = useMutation({
    mutationFn: () =>
      presentApi.fromTemplate(ws, {
        template: "project_plan_review",
        scope: `project:${projectKey}`,
        params: { project: projectKey },
      }),
    onSuccess: (p) => nav(`../../present/${p.id}`, { relative: "path" }),
    onError: (e) => toasts.error(e),
  });
  const approve = useMutation({
    mutationFn: () =>
      approveBaseline(ws, projectKey, `Approved ${new Date().toISOString().slice(0, 10)}`),
    onSuccess: () => {
      toasts.push("Plan approved: this is now the baseline");
      refresh();
    },
    onError: (e) => toasts.error(e),
  });
  const setDate = (r: PlanBarRow, field: "planned_start" | "planned_end", value: string) => {
    const start = field === "planned_start" ? value : r.planned_start;
    const end = field === "planned_end" ? value : r.planned_end;
    const msg = dateEditError(start || null, end || null);
    if (msg) return setError({ key: r.key, msg });
    setError(null);
    edit.mutate({ key: r.key, field, value });
  };

  if (plan.isLoading) return <p>Loading…</p>;
  if (!data || rows.length === 0)
    return (
      <div className="plan">
        <h1>{projectKey} plan</h1>
        <Empty>This project has no plan yet. Create one from the API or import a template.</Empty>
      </div>
    );
  const proj = data.project;
  return (
    <div className="plan">
      <div className="row">
        <h1>{projectKey} · plan</h1>
        <span className={`pill health-${proj.health}`} role="status">
          {HEALTH_LABEL[proj.health] ?? proj.health}
        </span>
        <span className="grow" />
        <Button onClick={() => present.mutate()} disabled={present.isPending}>
          Present this plan
        </Button>
        {canWrite && <Button onClick={() => approve.mutate()}>Approve plan (baseline)</Button>}
      </div>
      {proj.baseline_name && <small>Baseline: {proj.baseline_name}</small>}
      <Card title="Roadmap">
        <table aria-label="Roadmap">
          <thead>
            <tr>
              <th>Item</th>
              <th>Start</th>
              <th>End</th>
              <th>Status</th>
              <th style={{ width: "40%" }}>Timeline</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const bar = axis ? barOf(r, axis) : null;
              return (
                <tr key={r.key} className={r.level === 0 ? "phase" : "deliv"}>
                  <td style={{ paddingLeft: r.level * 20 }}>
                    <strong>{r.level === 0 ? r.title : ""}</strong>
                    {r.level === 1 ? r.title : ""} <small>{r.key}</small>
                  </td>
                  {(["planned_start", "planned_end"] as const).map((f) => (
                    <td key={f}>
                      <input
                        type="date"
                        aria-label={`${r.title} ${f === "planned_start" ? "start" : "end"}`}
                        defaultValue={r[f] ?? ""}
                        disabled={!canWrite || r.level === 0}
                        onBlur={(e) =>
                          e.target.value !== (r[f] ?? "") && setDate(r, f, e.target.value)
                        }
                      />
                    </td>
                  ))}
                  <td>
                    {STATUS_LABEL[r.status] ?? r.status}
                    {r.progress_pct != null ? ` · ${r.progress_pct}%` : ""}
                  </td>
                  <td>
                    {bar && (
                      <div className="track" aria-hidden="true">
                        <div
                          className={`plan-bar st-${r.status}`}
                          style={{ marginLeft: `${bar.left}%`, width: `${bar.width}%` }}
                        />
                      </div>
                    )}
                    {error && error.key === r.key && <small role="alert">{error.msg}</small>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <small>Phase dates follow their deliverables; edit a deliverable to move it.</small>
      </Card>
      {(data.milestones ?? []).length > 0 && (
        <Card title="Milestones">
          <ul className="plain" aria-label="Milestones">
            {data.milestones.map((m) => (
              <li key={m.id}>
                <strong>{m.name}</strong> · {m.due_date} · {STATUS_LABEL[m.status] ?? m.status} (
                {m.deliverables_done}/{m.deliverables_total} deliverables)
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
