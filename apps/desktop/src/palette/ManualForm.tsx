import { useState } from "react";
import {
  ACTIONS,
  DURATION_PRESETS,
  missingFields,
  parseMinutes,
  requiredFor,
  type Answers,
} from "./manual";
import type { Pack } from "./types";

/** Mode B. Choices are exactly the pack's lists; mandatory fields come from `pack.manual_mode` and are enforced here only
 * (the server accepts any valid file). Never calls the server, so it works offline. */
export function ManualForm({
  pack,
  initial,
  onSubmit,
  onSaveDraft,
  onOpenJson,
}: {
  pack: Pack;
  initial?: Answers;
  onSubmit: (a: Answers) => void;
  onSaveDraft: (a: Answers) => void;
  onOpenJson: (a: Answers) => void;
}) {
  const [a, setA] = useState<Answers>({ action: "note", ...initial });
  const [shown, setShown] = useState(false);
  const set = (k: string, v: Answers[string]) =>
    setA((x) => ({ ...x, [k]: v }));
  const missing = missingFields(pack, a);
  const required = new Set(requiredFor(pack, a));
  const err = (f: string) =>
    shown && missing.includes(f) ? (
      <small role="alert">{f.replace("_", " ")} is required</small>
    ) : null;
  const label = (f: string, text: string) =>
    `${text}${required.has(f) ? " *" : ""}`;
  const minutesBad =
    typeof a.minutes === "string" &&
    a.minutes !== "" &&
    parseMinutes(a.minutes) === null;
  const submit = () => {
    setShown(true);
    if (missing.length === 0 && !minutesBad) onSubmit(a);
  };
  return (
    <form
      aria-label="Manual form"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label>
        {label("action", "Action")}
        <select
          value={String(a.action ?? "")}
          onChange={(e) => set("action", e.target.value)}
        >
          {ACTIONS.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
      </label>
      {err("action")}
      <label>
        {label("project", "Project")}
        <select
          value={String(a.project ?? "")}
          onChange={(e) => set("project", e.target.value)}
        >
          <option value="">—</option>
          {pack.projects.map((p) => (
            <option key={p.key} value={p.key}>
              {p.key}
            </option>
          ))}
        </select>
      </label>
      {err("project")}
      <label>
        {label("item", "Task")}
        <select
          value={String(a.item ?? "")}
          onChange={(e) => set("item", e.target.value)}
        >
          <option value="">—</option>
          {pack.my_open_items.map((i) => (
            <option key={i.key} value={i.key}>
              {i.key} {i.title}
            </option>
          ))}
        </select>
      </label>
      {err("item")}
      <label>
        {label("minutes", "Time")}
        <input
          value={String(a.minutes ?? "")}
          placeholder="45m, 1h30"
          onChange={(e) => set("minutes", e.target.value)}
        />
      </label>
      <span>
        {DURATION_PRESETS.map((m) => (
          <button type="button" key={m} onClick={() => set("minutes", `${m}m`)}>
            {m < 60 ? `${m}m` : `${m / 60}h`}
          </button>
        ))}
      </span>
      {minutesBad ? <small role="alert">not a duration</small> : null}
      {err("minutes")}
      <fieldset>
        <legend>{label("tags", "Tags")}</legend>
        {pack.tags.map((t) => {
          const on = ((a.tags as string[]) ?? []).includes(t);
          return (
            <label key={t}>
              <input
                type="checkbox"
                checked={on}
                onChange={() =>
                  set(
                    "tags",
                    on
                      ? (a.tags as string[]).filter((x) => x !== t)
                      : [...((a.tags as string[]) ?? []), t],
                  )
                }
              />
              {t}
            </label>
          );
        })}
      </fieldset>
      <label>
        {label("planned_end", "Plan: ends")}
        <input
          value={String(a.planned_end ?? "")}
          placeholder="tomorrow 5pm"
          onChange={(e) => set("planned_end", e.target.value)}
        />
      </label>
      {err("planned_end")}
      <label>
        {label("note", "Note")}
        <textarea
          value={String(a.note ?? "")}
          onChange={(e) => set("note", e.target.value)}
        />
      </label>
      {err("note")}
      <div className="actions">
        <button type="submit">Send</button>
        <button type="button" onClick={() => onOpenJson(a)}>
          Open as JSON
        </button>
        <button type="button" onClick={() => onSaveDraft(a)}>
          Save draft to outbox
        </button>
      </div>
    </form>
  );
}
