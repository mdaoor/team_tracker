import { useMemo } from "react";
import { Button, Field } from "../../components/ui";
import { FilterBar } from "../views/Filters";
import {
  NOTIFY_ROLES,
  REPORTING_KINDS,
  SCOPE_TYPES,
  autoBody,
  changedLabel,
  type AutoAction,
  type Notify,
  type Scope,
} from "../../lib/governance";
import type { Chip } from "../../lib/viewState";
import { useList } from "../../lib/hooks";
import type { Row } from "../../api/http";

const LISTS: Record<string, { path: string; id: (r: Row) => string }> = {
  project: { path: "/projects", id: (r) => r.key },
  team: { path: "/teams", id: (r) => r.slug },
  product: { path: "/products", id: (r) => r.key },
  process: { path: "/processes", id: (r) => r.key },
};

/** Where a policy applies: the whole workspace, or one project / team / product / process. */
export function ScopePicker({ value, onChange }: { value: Scope; onChange: (s: Scope) => void }) {
  const src = LISTS[value.type];
  const options = useList(src?.path ?? "/projects", undefined, !!src);
  // a stored policy carries the row's internal id; the options are keyed by key/slug: show whichever matches
  const picked = (options.data ?? []).find((r) => r.id === value.id || src?.id(r) === value.id);
  return (
    <Field label="Applies to">
      <div className="row">
        <select
          aria-label="Policy scope type"
          value={value.type}
          onChange={(e) => onChange({ type: e.target.value, id: "" })}
        >
          {SCOPE_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        {src && (
          <select
            aria-label="Policy scope"
            value={picked && src ? src.id(picked) : value.id}
            onChange={(e) => onChange({ ...value, id: e.target.value })}
          >
            <option value="">choose…</option>
            {(options.data ?? []).map((r) => (
              <option key={src.id(r)} value={src.id(r)}>
                {r.name ?? src.id(r)}
              </option>
            ))}
          </select>
        )}
      </div>
    </Field>
  );
}

/** Narrows which targets are even considered (the same filter chips as the views). */
export function CandidateFilter({
  chips,
  onChange,
}: {
  chips: Chip[];
  onChange: (c: Chip[]) => void;
}) {
  return (
    <Field label="Only consider items matching">
      <FilterBar chips={chips} onChange={onChange} />
      {chips.length === 0 && <small>Every item in scope is considered.</small>}
    </Field>
  );
}

function toggle(list: string[], v: string): string[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

/** Who hears about a violation. Wellbeing policies ignore `roles` on the server (privacy rule), and the editor says so. */
export function NotifyEditor({
  value,
  onChange,
  wellbeing,
}: {
  value: Notify;
  onChange: (n: Notify) => void;
  wellbeing?: boolean;
}) {
  return (
    <fieldset>
      <legend>Notify</legend>
      <label>
        <input
          type="checkbox"
          checked={value.person}
          onChange={(e) => onChange({ ...value, person: e.target.checked })}
        />{" "}
        The person it is about
      </label>
      <div role="group" aria-label="Their managers">
        {REPORTING_KINDS.map((k) => (
          <label key={k}>
            <input
              type="checkbox"
              checked={value.reporting_line.includes(k)}
              onChange={() =>
                onChange({ ...value, reporting_line: toggle(value.reporting_line, k) })
              }
            />{" "}
            {k} manager
          </label>
        ))}
      </div>
      <div role="group" aria-label="Roles">
        {NOTIFY_ROLES.map((r) => (
          <label key={r}>
            <input
              type="checkbox"
              checked={value.roles.includes(r)}
              disabled={wellbeing}
              onChange={() => onChange({ ...value, roles: toggle(value.roles, r) })}
            />{" "}
            everyone with role {r.replace("_", " ")}
          </label>
        ))}
      </div>
      {wellbeing && (
        <small role="note">
          Wellbeing violations go only to the person and their line managers, whatever is chosen
          here.
        </small>
      )}
      <label>
        Channel{" "}
        <select
          value={value.channel}
          onChange={(e) => onChange({ ...value, channel: e.target.value })}
        >
          <option value="in_app">In app</option>
          <option value="email">Email</option>
          <option value="slack">Slack</option>
        </select>
      </label>
    </fieldset>
  );
}

/** What the system does by itself when the policy fires. Every action is an audited rule event. */
export function AutoActionEditor({
  value,
  onChange,
  stages,
  projects,
}: {
  value: AutoAction;
  onChange: (a: AutoAction) => void;
  stages: string[];
  projects: string[];
}) {
  const preview = useMemo(() => autoBody(value), [value]);
  return (
    <fieldset>
      <legend>Automatic action</legend>
      <label>
        Raise priority to{" "}
        <select
          value={value.priority}
          onChange={(e) => onChange({ ...value, priority: e.target.value })}
        >
          <option value="">don’t change</option>
          {["medium", "high", "highest", "critical"].map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </label>
      <label>
        Move to stage{" "}
        <select value={value.stage} onChange={(e) => onChange({ ...value, stage: e.target.value })}>
          <option value="">don’t move</option>
          {stages.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <label>
        Create a follow-up task{" "}
        <input
          value={value.createTitle}
          placeholder="title (empty = none)"
          onChange={(e) => onChange({ ...value, createTitle: e.target.value })}
        />
      </label>
      {value.createTitle.trim() && (
        <label>
          in project{" "}
          <select
            value={value.createProject}
            onChange={(e) => onChange({ ...value, createProject: e.target.value })}
          >
            <option value="">the violation’s project</option>
            {projects.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
      )}
      <small>
        {preview ? "Runs on every new violation of this policy." : "No automatic action."}
      </small>
    </fieldset>
  );
}

/** Edits to this policy, newest first, restorable. Restoring writes the old definition as a new edit (history is never rewritten). */
export function VersionHistory({
  versions,
  onRestore,
  canWrite,
}: {
  versions: Row[];
  onRestore: (v: Row) => void;
  canWrite: boolean;
}) {
  if (versions.length === 0) return <small>No history yet.</small>;
  return (
    <ol aria-label="Version history" className="plain">
      {versions.map((v, i) => (
        <li key={v.event_id}>
          <strong>{v.kind === "created" ? "Created" : "Edited"}</strong>{" "}
          <time dateTime={v.at}>{new Date(v.at).toLocaleString()}</time> ·{" "}
          {v.kind === "created" ? "initial definition" : changedLabel(v.changed ?? [])}
          {i > 0 && canWrite && v.definition && (
            <Button variant="ghost" onClick={() => onRestore(v)}>
              Restore this version
            </Button>
          )}
        </li>
      ))}
    </ol>
  );
}
