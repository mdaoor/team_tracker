import { useState } from "react";
import { CATEGORY_LABEL, clock, hm } from "./format";
import type { TrayEntry } from "./types";

export interface TrayActions {
  onSelect: (id: string) => void;
  onSend: (
    e: TrayEntry,
    opts?: { project?: string; note?: string; minutes?: number },
  ) => void;
  onNotWork: (e: TrayEntry) => void;
}

function cue(e: TrayEntry): string {
  const s = e.suggestion;
  if (!s.project) return "ask";
  return `${s.cue === "✓" ? "✓ " : ""}${Math.round(s.confidence * 100)} %${s.cue === "✓" ? "" : ` ${s.cue}`}`;
}

/** "Ready to send": a finished meeting or done work with a project suggestion. [Send] is one tap; a project chip row appears only when
 * no project is known (three choices from the pack); [Edit] opens minutes and note in place; [Not work] teaches the rule. */
export function Tray({
  entries,
  selected,
  actions,
  heading = "Ready to send",
}: {
  entries: TrayEntry[];
  selected: string | null;
  actions: TrayActions;
  heading?: string;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");

  const edit = (e: TrayEntry) => {
    setEditing(editing === e.id ? null : e.id);
    setMinutes(String(e.minutes));
    setNote(e.note);
  };

  return (
    <section className="pt-tray" aria-labelledby="tray-h">
      <h2 id="tray-h">
        {heading} <span className="muted">({entries.length})</span>
      </h2>
      {entries.length === 0 ? (
        <p className="muted">
          Nothing to send. Finished meetings and work you mark done appear here.
        </p>
      ) : (
        <ul className="pt-list" data-list="tray" style={{ marginTop: 8 }}>
          {entries.map((e) => (
            <li
              key={e.id}
              className="pt-card"
              data-row
              data-tray-id={e.id}
              data-selected={selected === e.id}
              tabIndex={0}
              aria-label={`${e.label}. ${cue(e)}`}
              onFocus={() => actions.onSelect(e.id)}
              onClick={() => actions.onSelect(e.id)}
            >
              <span className="label">{e.label}?</span>
              <span className="muted">
                {CATEGORY_LABEL[e.category]} ·{" "}
                {e.start ? clock(e.start) : "anytime"} · {hm(e.minutes)} ·{" "}
                {cue(e)}
                {e.suggestion.reasons[0] ? ` · ${e.suggestion.reasons[0]}` : ""}
              </span>
              <span className="muted">“{e.note}”</span>
              {e.needs_project ? (
                <fieldset className="pt-chips">
                  <legend>Which project?</legend>
                  {e.suggestion.alternatives.slice(0, 3).map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => actions.onSend(e, { project: p })}
                    >
                      {p}
                      <span className="sr-only"> for {e.title}</span>
                    </button>
                  ))}
                </fieldset>
              ) : null}
              {editing === e.id ? (
                <div className="pt-edit">
                  <label>
                    Minutes
                    <input
                      type="text"
                      inputMode="numeric"
                      value={minutes}
                      onChange={(ev) => setMinutes(ev.target.value)}
                      data-autofocus
                    />
                  </label>
                  <label>
                    Note
                    <input
                      type="text"
                      value={note}
                      onChange={(ev) => setNote(ev.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="primary"
                    onClick={() => {
                      const m = Number(minutes);
                      actions.onSend(e, {
                        note,
                        ...(m > 0 ? { minutes: m } : {}),
                      });
                      setEditing(null);
                    }}
                  >
                    Send with these changes
                    <span className="sr-only"> for {e.title}</span>
                  </button>
                </div>
              ) : null}
              <span
                className="actions"
                style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
              >
                {!e.needs_project ? (
                  <button
                    type="button"
                    className="primary"
                    onClick={() => actions.onSend(e)}
                  >
                    Send<span className="sr-only"> {e.title}</span>
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => edit(e)}
                  aria-expanded={editing === e.id}
                >
                  Edit<span className="sr-only"> {e.title}</span>
                </button>
                <button
                  type="button"
                  className="quiet"
                  onClick={() => actions.onNotWork(e)}
                >
                  Not work<span className="sr-only">: {e.title}</span>
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
