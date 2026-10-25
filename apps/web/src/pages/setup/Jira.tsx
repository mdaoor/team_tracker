import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import * as jiraApi from "../../api/jira";
import type { JiraConnection, Json } from "../../api/types";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty, Field } from "../../components/ui";
import { useList, useObj, useWs } from "../../lib/hooks";

const MAPS = [
  ["issuetype_map", "Item type → Jira issue type"],
  ["priority_map", "Priority → Jira priority"],
  ["status_map", "Workflow state → Jira status"],
] as const;
type MapName = (typeof MAPS)[number][0];
type Pair = { k: string; v: string };

const toPairs = (m: Record<string, string> | undefined): Pair[] =>
  Object.entries(m ?? {}).map(([k, v]) => ({ k, v }));
const fromPairs = (p: Pair[]): Record<string, string> =>
  Object.fromEntries(
    p.filter((x) => x.k.trim() && x.v.trim()).map((x) => [x.k.trim(), x.v.trim()]),
  );

export function Jira() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const conn = useObj<JiraConnection>("/integrations/jira");
  const projects = useList("/projects");
  const log = useList("/integrations/jira/log", { limit: 50 });
  const [baseUrl, setBaseUrl] = useState("");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [project, setProject] = useState("");
  const [opts, setOpts] = useState<Json | null>(null);

  useEffect(() => {
    if (conn.data?.configured) {
      setBaseUrl(conn.data.base_url);
      setEmail(conn.data.email);
    }
  }, [conn.data]);

  const save = useMutation({
    mutationFn: () =>
      jiraApi.saveJiraConnection(ws, {
        base_url: baseUrl,
        email,
        api_token: token || undefined,
        enabled: true,
      }),
    onSuccess: (r) => {
      setToken("");
      if (r.webhook_secret) setSecret(r.webhook_secret);
      void qc.invalidateQueries({ queryKey: [ws, "/integrations/jira"] });
      toasts.push("Saved");
    },
    onError: (e) => toasts.error(e),
  });
  const test = useMutation({
    mutationFn: () => jiraApi.testJira(ws, project),
    onSuccess: (r) => {
      if (r.ok) {
        setOpts(r.options ?? null);
        toasts.push(`Connected as ${r.account?.name ?? "the integration user"}`);
      } else toasts.error(new Error(r.error ?? "Connection failed"));
      void qc.invalidateQueries({ queryKey: [ws, "/integrations/jira"] });
    },
    onError: (e) => toasts.error(e),
  });
  const rotate = useMutation({
    mutationFn: () => jiraApi.rotateJiraSecret(ws),
    onSuccess: (r) => setSecret(r.webhook_secret),
    onError: (e) => toasts.error(e),
  });

  return (
    <div className="gov">
      <Card title="Connection">
        <p>
          <small>
            Jira is a projection of Team Tracker: items are pushed to Jira, and only status,
            assignee and comments come back. The token is stored encrypted and never shown again.
          </small>
        </p>
        <Field label="Jira URL">
          <input
            value={baseUrl}
            placeholder="https://your-team.atlassian.net"
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </Field>
        <Field label="Account e-mail">
          <input value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={conn.data?.configured ? "API token (leave empty to keep)" : "API token"}>
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <div className="row">
          <Button
            variant="primary"
            disabled={!baseUrl || !email || (!conn.data?.configured && !token) || save.isPending}
            onClick={() => save.mutate()}
          >
            Save connection
          </Button>
          <Button disabled={!conn.data?.configured || test.isPending} onClick={() => test.mutate()}>
            Test connection
          </Button>
          {conn.data?.configured && (
            <Button variant="ghost" onClick={() => rotate.mutate()}>
              New webhook secret
            </Button>
          )}
        </div>
        {conn.data?.configured && (
          <p>
            <small>
              Webhook (Jira → Settings → System → Webhooks, events: issue updated, comment created):{" "}
              <code>
                {window.location.origin}
                {conn.data.webhook_path}?token={secret || "‹shown once when created or rotated›"}
              </code>
            </small>
          </p>
        )}
      </Card>

      <Card title="Project mapping">
        <div className="row">
          <select
            aria-label="Project"
            value={project}
            onChange={(e) => {
              setProject(e.target.value);
              setOpts(null);
            }}
          >
            <option value="">Choose a project…</option>
            {(projects.data ?? []).map((p) => (
              <option key={p.id} value={p.key}>
                {p.key} · {p.name}
              </option>
            ))}
          </select>
        </div>
        {project && (
          <Mapping key={project} project={project} opts={opts} onFetch={() => test.mutate()} />
        )}
      </Card>

      <Card title="Sync log (last 50)">
        {(log.data ?? []).length === 0 ? (
          <Empty>Nothing pushed yet.</Empty>
        ) : (
          <table aria-label="Sync log">
            <thead>
              <tr>
                <th>When</th>
                <th>Issue</th>
                <th>What</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {(log.data ?? []).map((r) => (
                <tr key={r.id}>
                  <td>{String(r.created_at).slice(0, 16).replace("T", " ")}</td>
                  <td>{r.issue_key ?? "—"}</td>
                  <td>
                    {r.direction} · {r.action}
                    <br />
                    <small>{Object.keys(r.diff ?? {}).length ? JSON.stringify(r.diff) : ""}</small>
                  </td>
                  <td>{r.ok ? "ok" : (r.error ?? "failed")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Mapping({
  project,
  opts,
  onFetch,
}: {
  project: string;
  opts: Json | null;
  onFetch: () => void;
}) {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const cur = useObj<JiraConnection>(`/projects/${project}/jira`);
  const [cfg, setCfg] = useState<Json>({});
  const [maps, setMaps] = useState<Record<MapName, Pair[]>>({
    issuetype_map: [],
    priority_map: [],
    status_map: [],
  });
  useEffect(() => {
    const c = (cur.data?.config ?? {}) as Json;
    setCfg(c);
    setMaps({
      issuetype_map: toPairs(c.issuetype_map),
      priority_map: toPairs(c.priority_map),
      status_map: toPairs(c.status_map),
    });
  }, [cur.data]);
  const save = useMutation({
    mutationFn: () =>
      jiraApi.saveProjectJira(ws, project, {
        ...cfg,
        enabled: !!cfg.enabled,
        auto_send: !!cfg.auto_send,
        sync_time_logs: !!cfg.sync_time_logs,
        sync_comments: !!cfg.sync_comments,
        issuetype_map: fromPairs(maps.issuetype_map),
        priority_map: fromPairs(maps.priority_map),
        status_map: fromPairs(maps.status_map),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [ws, `/projects/${project}/jira`] });
      toasts.push("Mapping saved");
    },
    onError: (e) => toasts.error(e),
  });
  const choices: Record<MapName, string[]> = {
    issuetype_map: opts?.issuetypes ?? [],
    priority_map: opts?.priorities ?? [],
    status_map: opts?.statuses ?? [],
  };
  const flag = (k: string, label: string) => (
    <label>
      <input
        type="checkbox"
        checked={!!cfg[k]}
        onChange={(e) => setCfg({ ...cfg, [k]: e.target.checked })}
      />{" "}
      {label}
    </label>
  );
  return (
    <div>
      <Field label="Jira project key">
        <input
          value={cfg.project_key ?? ""}
          onChange={(e) => setCfg({ ...cfg, project_key: e.target.value.toUpperCase() })}
        />
      </Field>
      <div className="row">
        {flag("enabled", "Linked")}
        {flag("auto_send", "Push every item (otherwise only items the reporter sends)")}
        {flag("sync_time_logs", "Time entries → worklogs")}
        {flag("sync_comments", "Jira comments → comments")}
      </div>
      <Field label="Delivery stage custom field id (optional)">
        <input
          value={cfg.stage_field ?? ""}
          placeholder="customfield_10001"
          onChange={(e) => setCfg({ ...cfg, stage_field: e.target.value || null })}
        />
      </Field>
      <div className="row">
        <Button onClick={onFetch} disabled={!cfg.project_key}>
          Fetch Jira options
        </Button>
        {opts && (
          <small>
            {opts.statuses.length} statuses · {opts.issuetypes.length} issue types loaded
          </small>
        )}
      </div>
      {MAPS.map(([name, title]) => (
        <section key={name} aria-label={title}>
          <h3>{title}</h3>
          <datalist id={`opts-${name}`}>
            {choices[name].map((o) => (
              <option key={o} value={o} />
            ))}
          </datalist>
          {maps[name].map((p, i) => (
            <div key={i} className="row">
              <input
                aria-label={`${title} key`}
                value={p.k}
                onChange={(e) =>
                  setMaps({
                    ...maps,
                    [name]: maps[name].map((x, j) => (j === i ? { ...x, k: e.target.value } : x)),
                  })
                }
              />
              →
              <input
                aria-label={`${title} value`}
                list={`opts-${name}`}
                value={p.v}
                onChange={(e) =>
                  setMaps({
                    ...maps,
                    [name]: maps[name].map((x, j) => (j === i ? { ...x, v: e.target.value } : x)),
                  })
                }
              />
              <Button
                variant="ghost"
                aria-label="Remove row"
                onClick={() => setMaps({ ...maps, [name]: maps[name].filter((_, j) => j !== i) })}
              >
                ✕
              </Button>
            </div>
          ))}
          <Button onClick={() => setMaps({ ...maps, [name]: [...maps[name], { k: "", v: "" }] })}>
            Add row
          </Button>
        </section>
      ))}
      <div className="row">
        <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
          Save mapping
        </Button>
      </div>
    </div>
  );
}
