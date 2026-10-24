import { useState } from "react";
import { Button, Chip as UiChip } from "../../components/ui";
import { useList } from "../../lib/hooks";
import { chipId, type Chip } from "../../lib/viewState";

type Kind =
  "project" | "stage" | "state" | "priority" | "assignee" | "team" | "tag" | "text" | "due" | "dim";
const KINDS: [Kind, string][] = [
  ["project", "Project"],
  ["stage", "Stage"],
  ["state", "State"],
  ["priority", "Priority ≥"],
  ["assignee", "Assignee"],
  ["team", "Team (members)"],
  ["tag", "Tag"],
  ["text", "Text"],
  ["due", "Due before"],
  ["dim", "Dimension"],
]; // fmt: skip

export function FilterBar({ chips, onChange }: { chips: Chip[]; onChange: (c: Chip[]) => void }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Kind>("project");
  const [dimKey, setDimKey] = useState("");
  const [value, setValue] = useState("");
  const projects = useList("/projects");
  const people = useList("/people");
  const teams = useList("/teams");
  const dims = useList("/dimensions");
  const add = () => {
    if (!value && kind !== "dim") return;
    const v = value.trim();
    const defs: Record<Kind, [string, Chip["clause"]]> = {
      project: [`project ${v}`, { project: { in: [v] } }],
      stage: [`stage ${v}`, { stage: { in: [v] } }],
      state: [`state ${v}`, { state: { category: [v] } }],
      priority: [`priority ≥ ${v}`, { priority: { gte: v } }],
      assignee: [`assignee ${v}`, { people: { assignee: v } }],
      team: [`team ${v}`, { people: { assignee_in_team: v } }],
      tag: [`tag ${v}`, { tags: { any: [v] } }],
      text: [`“${v}”`, { text: v }],
      due: [`due before ${v}`, { due_date: { before: v } }],
      dim: [`${dimKey} = ${v}`, { dims: { [dimKey]: { in: [v] } } }],
    };
    const [label, clause] = defs[kind];
    onChange([...chips, { id: chipId(), label, clause }]);
    setValue("");
    setOpen(false);
  };
  const options: Partial<Record<Kind, string[]>> = {
    project: (projects.data ?? []).map((p) => p.key),
    stage: ["idea", "discovery", "design", "build", "test", "staging", "production"],
    state: ["open", "active", "done"],
    priority: ["low", "medium", "high", "highest", "critical"],
    assignee: ["me", ...(people.data ?? []).map((p) => p.handle)],
    team: (teams.data ?? []).map((t) => t.slug),
  };
  return (
    <div className="chips row" role="group" aria-label="Filters">
      {chips.map((c) => (
        <UiChip key={c.id} onRemove={() => onChange(chips.filter((x) => x.id !== c.id))}>
          <button
            className="chip-neg"
            aria-label={`negate ${c.label}`}
            aria-pressed={!!c.negate}
            onClick={() =>
              onChange(chips.map((x) => (x.id === c.id ? { ...x, negate: !x.negate } : x)))
            }
          >
            ¬
          </button>
          <span style={{ textDecoration: c.negate ? "line-through" : undefined }}>{c.label}</span>
        </UiChip>
      ))}
      {open ? (
        <span className="row">
          <select
            aria-label="Filter on"
            value={kind}
            onChange={(e) => (setKind(e.target.value as Kind), setValue(""))}
          >
            {KINDS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
          {kind === "dim" && (
            <select
              aria-label="Dimension"
              value={dimKey}
              onChange={(e) => setDimKey(e.target.value)}
            >
              <option value="">dimension…</option>
              {(dims.data ?? []).map((d) => (
                <option key={d.key}>{d.key}</option>
              ))}
            </select>
          )}
          {options[kind] ? (
            <select aria-label="Value" value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">value…</option>
              {options[kind]!.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ) : (
            <input
              aria-label="Value"
              value={value}
              placeholder={kind === "due" ? "today, +7d, 2026-12-01" : "value"}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
            />
          )}
          <Button variant="primary" onClick={add}>
            Add
          </Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </span>
      ) : (
        <Button onClick={() => setOpen(true)}>+ Filter</Button>
      )}
    </div>
  );
}
