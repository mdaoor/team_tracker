import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, w, type Json } from "../../api/http";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty, Field, Pill } from "../../components/ui";
import type { Severity } from "../../lib/confidence";
import {
  autoBody,
  autoFrom,
  canActivate,
  chipsFromFilter,
  controlFor,
  emptyAuto,
  emptyNotify,
  notifyBody,
  notifyFrom,
  paramsFrom,
  restoreBody,
  type AutoAction,
  type CelProblem,
  type Control,
  type Notify,
  type Scope,
} from "../../lib/governance";
import { chipId, toFilter, type Chip } from "../../lib/viewState";
import {
  AutoActionEditor,
  CandidateFilter,
  NotifyEditor,
  ScopePicker,
  VersionHistory,
} from "./PolicyEditors";
import { useList, useMe, useObj, useWs } from "../../lib/hooks";
import { CelEditor } from "./CelEditor";

export function PoliciesRoute() {
  const rest = useParams()["*"] ?? "";
  if (rest === "new") return <PolicyForm />;
  if (rest) return <PolicyForm existing={rest} />;
  return <PolicyList />;
}

function PolicyList() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can } = useMe();
  const pol = useList("/policies");
  const open = useList("/violations", { status: "open,acknowledged", limit: 500 });
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const v of open.data ?? []) c[v.policy] = (c[v.policy] ?? 0) + 1;
    return c;
  }, [open.data]);
  const toggle = useMutation({
    mutationFn: ({ key, on }: { key: string; on: boolean }) =>
      api.post(w(ws, `/policies/${key}:${on ? "activate" : "deactivate"}`)),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [ws, "/policies"] }),
    onError: (e) => toasts.error(e),
  });
  return (
    <div className="gov">
      <div className="row">
        <h1>Policies</h1>
        <span className="grow" />
        {can["policy.write"] && (
          <Link className="btn btn-primary" to="new">
            New policy
          </Link>
        )}
      </div>
      {(pol.data ?? []).length === 0 ? (
        <Empty>No policies yet. Start from a template.</Empty>
      ) : (
        <table aria-label="Policies">
          <thead>
            <tr>
              <th>Policy</th>
              <th>Category</th>
              <th>Severity</th>
              <th>Last dry run</th>
              <th>Open violations</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(pol.data ?? []).map((p) => (
              <tr key={p.key}>
                <td>
                  <Link to={p.key}>{p.name}</Link>
                  <br />
                  <small>{p.key}</small>
                </td>
                <td>{p.category}</td>
                <td>
                  <Pill severity={p.severity as Severity}>{p.severity}</Pill>
                </td>
                <td>
                  {p.last_test
                    ? `${p.last_test.fired_count} hit${p.last_test.fired_count === 1 ? "" : "s"}`
                    : "never"}
                </td>
                <td>
                  <Link to={`../violations?policy=${p.key}`}>{counts[p.key] ?? 0}</Link>
                </td>
                <td>{p.is_active ? "Active" : "Inactive"}</td>
                <td>
                  {can["policy.write"] && p.is_active && (
                    <Button onClick={() => toggle.mutate({ key: p.key, on: false })}>
                      Deactivate
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function PolicyForm({ existing }: { existing?: string }) {
  const ws = useWs();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toasts = useToasts();
  const [sp, setSp] = useSearchParams();
  const { can, admin } = useMe();
  const templates = useList("/policy-templates");
  const schema = useObj("/governance/context-schema", { version: 1 });
  const stages: Json[] = (useList("/processes").data ?? []).flatMap((p) => p.stages ?? []);
  const cur = useObj(`/policies/${existing}`, undefined, !!existing);
  const policy = cur.data as Json | undefined;
  const [mode, setMode] = useState<"template" | "cel">("template");
  const [tplKey, setTplKey] = useState<string>(sp.get("template") ?? "");
  const [values, setValues] = useState<Record<string, string>>({});
  const [cel, setCel] = useState("");
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [sev, setSev] = useState("");
  const [grace, setGrace] = useState("");
  const [scope, setScope] = useState<Scope>({ type: "workspace", id: "" });
  const [chips, setChips] = useState<Chip[]>([]);
  const [notify, setNotify] = useState<Notify>({ ...emptyNotify(), reporting_line: ["line"] });
  const [auto, setAuto] = useState<AutoAction>(emptyAuto());
  const versions = useList(`/policies/${existing}/versions`, undefined, !!existing);
  const projectKeys = (useList("/projects").data ?? []).map((p) => p.key as string);
  const [problems, setProblems] = useState<CelProblem[]>([]);
  const [compiled, setCompiled] = useState("");
  const [describe, setDescribe] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [run, setRun] = useState<Json | null>(null);
  const tpl = (templates.data ?? []).find((t) => t.key === tplKey);
  const controls = useMemo(
    () =>
      (tpl?.params ?? []).map((p: Json) =>
        controlFor(
          p,
          stages.map((s) => s.key),
        ),
      ),
    [tpl, stages],
  );

  useEffect(() => {
    if (!policy) return;
    setName(policy.name);
    setKey(policy.key);
    setSev(policy.severity);
    setGrace(policy.grace_period ?? "");
    setCel(policy.condition_cel);
    setMode(policy.condition_template_id ? "template" : "cel");
    const t = (templates.data ?? []).find((x) => x.id === policy.condition_template_id);
    if (t) setTplKey(t.key);
    setValues(
      Object.fromEntries(
        Object.entries(policy.template_params ?? {}).map(([k, v]) => [k, String(v)]),
      ),
    );
    setAuto(autoFrom(policy.auto_action));
    setNotify(notifyFrom(policy.notify));
    setScope({
      type: policy.scope_type ?? "workspace",
      id: policy.scope_ref ?? policy.scope_id ?? "",
    });
    setChips(chipsFromFilter(policy.candidate_filter, chipId) as Chip[]);
  }, [policy, templates.data]);

  // debounce 400 ms → /policies:check (Phase 9: inline errors within 1 s of typing)
  const checkBody =
    mode === "template" ? { template: tplKey, params: paramsFrom(controls, values) } : { cel };
  const wantCheck = mode === "template" ? !!tplKey : cel.trim() !== "";
  const sig = JSON.stringify(checkBody);
  useEffect(() => {
    if (!wantCheck) {
      setProblems([]);
      return;
    }
    const id = setTimeout(() => {
      api
        .post<Json>(w(ws, "/policies:check"), JSON.parse(sig))
        .then((r) => {
          setProblems(r.errors ?? []);
          if (mode === "template") setCompiled(r.cel ?? "");
        })
        .catch((e) => setProblems([{ message: String(e.message ?? e) }]));
    }, 400);
    return () => clearTimeout(id);
  }, [sig, wantCheck, ws, mode]);

  const body = (): Json => ({
    key,
    name,
    severity: sev || undefined,
    grace_period: grace || undefined,
    notify: notifyBody(notify),
    auto_action: autoBody(auto),
    scope_type: scope.type,
    scope_id: scope.id || undefined,
    candidate_filter: chips.length ? toFilter(chips) : undefined,
    ...(mode === "template"
      ? { template: tplKey, params: paramsFrom(controls, values) }
      : { condition_cel: cel }),
  });
  const save = useMutation({
    mutationFn: async () => {
      if (existing) {
        const b = body();
        return api.patch<Json>(w(ws, `/policies/${existing}`), {
          name: b.name,
          severity: b.severity,
          grace_period: b.grace_period ?? null,
          notify: b.notify,
          auto_action: b.auto_action ?? null,
          scope_type: b.scope_type,
          scope_id: b.scope_id ?? null,
          candidate_filter: b.candidate_filter ?? null,
          ...(mode === "template" ? { params: b.params } : { condition_cel: cel }),
        });
      }
      return api.post<Json>(w(ws, "/policies"), body());
    },
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: [ws, "/policies"] });
      void qc.invalidateQueries({ queryKey: [ws, `/policies/${p.key}`] });
      void qc.invalidateQueries({ queryKey: [ws, `/policies/${p.key}/versions`] });
      toasts.push("Saved");
      if (!existing) nav(`../${p.key}`, { relative: "path" });
    },
    onError: (e) => toasts.error(e),
  });
  const test = useMutation({
    mutationFn: () => api.post<Json>(w(ws, `/policies/${existing}:test`), { window: "7d" }),
    onSuccess: (r) => {
      setRun(r);
      void qc.invalidateQueries({ queryKey: [ws, `/policies/${existing}`] });
    },
    onError: (e) => toasts.error(e),
  });
  const activate = useMutation({
    mutationFn: (force: boolean) =>
      api.post<Json>(w(ws, `/policies/${existing}:activate${force ? "?force=true" : ""}`)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [ws, `/policies/${existing}`] });
      void qc.invalidateQueries({ queryKey: [ws, "/policies"] });
      toasts.push("Policy activated");
    },
    onError: (e) => toasts.error(e),
  });
  const restore = useMutation({
    mutationFn: (v: Json) =>
      api.patch<Json>(w(ws, `/policies/${existing}`), restoreBody(v.definition)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [ws, `/policies/${existing}`] });
      void qc.invalidateQueries({ queryKey: [ws, `/policies/${existing}/versions`] });
      toasts.push("Restored. Run a dry run before activating again.");
    },
    onError: (e) => toasts.error(e),
  });
  const author = useMutation({
    mutationFn: () => api.post<Json>(w(ws, "/policies:author"), { request: describe }),
    onSuccess: (r) => {
      const d = r.draft ?? {};
      if (d.mode === "template" && d.template) {
        setMode("template");
        setTplKey(d.template);
        setValues(
          Object.fromEntries(Object.entries(d.params ?? {}).map(([k, v]) => [k, String(v)])),
        );
      } else if (d.cel) {
        setMode("cel");
        setCel(d.cel);
      }
      setDraftNote(
        r.errors?.length
          ? `Draft has problems: ${r.errors[0].message}. Review before saving.`
          : "Draft filled in below. Review it, then save.",
      );
    },
    onError: (e) => toasts.error(e),
  });

  const gate = policy ? canActivate(policy, admin) : "no";
  const sample = (run?.sample_target_ids ?? []) as string[];
  const canWrite = !!can["policy.write"];

  return (
    <div className="gov policy-form">
      <h1>{existing ? `Policy · ${policy?.name ?? existing}` : "New policy"}</h1>
      {!existing && (
        <Card title="Describe the rule (draft)">
          <div className="row">
            <input
              aria-label="Describe the rule"
              className="grow"
              placeholder="e.g. flag anything in Test for more than 3 days"
              value={describe}
              onChange={(e) => setDescribe(e.target.value)}
            />
            <Button
              disabled={describe.trim().length < 3 || author.isPending}
              onClick={() => author.mutate()}
            >
              Draft it
            </Button>
          </div>
          {draftNote && <p role="status">{draftNote}</p>}
        </Card>
      )}
      <Card
        title="Rule"
        actions={
          can["policy.admin"] && (
            <Button
              variant="ghost"
              onClick={() => setMode(mode === "template" ? "cel" : "template")}
            >
              {mode === "template" ? "Edit as CEL" : "Use a template"}
            </Button>
          )
        }
      >
        {mode === "template" ? (
          <>
            {!existing && (
              <div className="gallery" role="group" aria-label="Templates">
                {(templates.data ?? []).map((t) => (
                  <button
                    key={t.key}
                    aria-pressed={t.key === tplKey}
                    className={`tile ${t.key === tplKey ? "on" : ""}`}
                    onClick={() => {
                      setTplKey(t.key);
                      setValues({});
                      setName((n) => n || t.name);
                      setKey((k) => k || t.key);
                      setSev(t.severity);
                      setSp((p) => (p.set("template", t.key), p), { replace: true });
                    }}
                  >
                    <strong>{t.name}</strong>
                    <small>{t.category}</small>
                  </button>
                ))}
              </div>
            )}
            {tpl && (
              <>
                <p>{tpl.description}</p>
                {controls.map((c: Control) => (
                  <Field key={c.name} label={c.name}>
                    {c.kind === "select" ? (
                      <select
                        value={values[c.name] ?? ""}
                        onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}
                      >
                        <option value="">default</option>
                        {c.options?.map((o) => (
                          <option key={o}>{o}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={c.kind}
                        min={c.min}
                        max={c.max}
                        step={c.step}
                        placeholder={c.placeholder}
                        value={values[c.name] ?? ""}
                        onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}
                      />
                    )}
                    {c.help && <small>{c.help}</small>}
                  </Field>
                ))}
                {tpl.example && (
                  <p>
                    <small>Example hit: {tpl.example}</small>
                  </p>
                )}
                {can["policy.admin"] && (
                  <details>
                    <summary>Compiled expression</summary>
                    <CelEditor value={compiled} readOnly problems={[]} label="Compiled CEL" />
                  </details>
                )}
              </>
            )}
          </>
        ) : (
          <CelEditor value={cel} onChange={setCel} schema={schema.data} problems={problems} />
        )}
        <ul className="problems" aria-live="polite">
          {problems.map((p, i) => (
            <li key={i} role="alert">
              {p.field ? <strong>{p.field}: </strong> : null}
              {p.message}
              {p.line ? ` (line ${p.line}${p.column ? `, col ${p.column}` : ""})` : ""}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Details">
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        {!existing && (
          <Field label="Key">
            <input value={key} onChange={(e) => setKey(e.target.value)} pattern="[a-z][a-z0-9_]+" />
          </Field>
        )}
        <Field label="Severity">
          <select value={sev} onChange={(e) => setSev(e.target.value)}>
            {["info", "warning", "serious", "critical"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Field label="Grace period (e.g. 2d)">
          <input value={grace} onChange={(e) => setGrace(e.target.value)} placeholder="none" />
        </Field>
        <ScopePicker value={scope} onChange={setScope} />
        <CandidateFilter chips={chips} onChange={setChips} />
        <NotifyEditor
          value={notify}
          onChange={setNotify}
          wellbeing={policy?.category === "wellbeing"}
        />
        <AutoActionEditor
          value={auto}
          onChange={setAuto}
          stages={stages.map((x) => x.key as string)}
          projects={projectKeys}
        />
        <div className="row">
          <Button
            variant="primary"
            disabled={
              !canWrite || !name || !key || problems.length > 0 || !wantCheck || save.isPending
            }
            onClick={() => save.mutate()}
          >
            {existing ? "Save changes" : "Create policy"}
          </Button>
        </div>
      </Card>
      {existing && policy && (
        <Card title="Dry run">
          <p>
            Runs the rule against today’s data and writes nothing.
            {policy.trigger === "on_event" &&
              " (Event rules are judged on current state: an approximation.)"}
          </p>
          <div className="row">
            <Button onClick={() => test.mutate()} disabled={test.isPending || !canWrite}>
              Run dry run
            </Button>
            {policy.is_active ? (
              <span>Active</span>
            ) : (
              <>
                <Button
                  variant="primary"
                  disabled={gate !== "yes" || !canWrite}
                  onClick={() => activate.mutate(false)}
                >
                  Activate
                </Button>
                {gate === "force" && (
                  <Button onClick={() => activate.mutate(true)}>Activate anyway</Button>
                )}
              </>
            )}
          </div>
          {gate === "no" && !policy.is_active && (
            <small>Run a dry run after your last edit to enable activation.</small>
          )}
          {run && (
            <div role="status">
              <p>
                <strong data-testid="fired">{run.fired_count}</strong> would be flagged out of{" "}
                {run.evaluated} checked.
              </p>
              {run.errors?.length > 0 && (
                <p role="alert">{run.errors[0].message ?? JSON.stringify(run.errors[0])}</p>
              )}
              {sample.length > 0 && (
                <ul className="plain" aria-label="Sample targets">
                  {sample.slice(0, 50).map((id) => (
                    <li key={id}>
                      <Link to={`?item=${id}`}>{id}</Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </Card>
      )}
      {existing && policy && (
        <Card title="Version history">
          <VersionHistory
            versions={versions.data ?? []}
            canWrite={canWrite}
            onRestore={(v) => restore.mutate(v)}
          />
        </Card>
      )}
    </div>
  );
}
