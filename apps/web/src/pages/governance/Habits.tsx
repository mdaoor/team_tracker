import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, w, type Row } from "../../api/http";
import { Drawer } from "../../components/Drawer";
import { useToasts } from "../../components/Toasts";
import { Button, Empty } from "../../components/ui";
import { HABIT_FLOW, habitsCsv, trendGlyph } from "../../lib/governance";
import { useList, useMe, useWs } from "../../lib/hooks";

export function Habits() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can, me } = useMe();
  const [windowSel, setWindow] = useState("30d");
  const [subject, setSubject] = useState("");
  const habits = useList("/habits", { window: windowSel, ...(subject ? { subject } : {}) });
  const defs = useList("/habit-definitions");
  const viol = useList("/violations", { status: "open,acknowledged,resolved,waived", limit: 500 });
  const [open, setOpen] = useState<Row | null>(null);
  const [note, setNote] = useState("");
  const wellbeing = useMemo(
    () => new Set((defs.data ?? []).filter((d) => d.category === "wellbeing").map((d) => d.key)),
    [defs.data],
  );
  const rows = useMemo(() => habits.data ?? [], [habits.data]);
  const bySubject = useMemo(() => {
    const m = new Map<string, Row[]>();
    for (const h of rows) m.set(h.subject, [...(m.get(h.subject) ?? []), h]);
    return [...m.entries()].map(([s, hs]) => ({
      subject: s,
      habits: [...hs].sort((a, b) =>
        a.polarity === b.polarity ? a.key.localeCompare(b.key) : a.polarity === "positive" ? -1 : 1,
      ),
    }));
  }, [rows]);
  const hasWellbeing = (defs.data ?? []).some((d) => d.category === "wellbeing");
  const restricted = hasWellbeing && !rows.some((h) => wellbeing.has(h.key));

  useEffect(() => setNote(open?.coaching_note ?? ""), [open]);
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Row }) =>
      api.patch<Row>(w(ws, `/habits/${id}`), body),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: [ws, "/habits"] });
      setOpen((o) => (o ? { ...o, ...r, subject: o.subject } : o));
      toasts.push("Saved");
    },
    onError: (e) => toasts.error(e),
  });
  const linked = useMemo(() => {
    const ids = new Set<string>(open?.linked_violation_ids ?? []);
    return (viol.data ?? []).filter((v) => ids.has(v.id));
  }, [open, viol.data]);
  const exportCsv = () => {
    const blob = new Blob([habitsCsv(rows, me?.handle ?? "?")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `habits-${windowSel}.csv`;
    a.click();
  };

  return (
    <div className="gov">
      <div className="row">
        <h1>Habits</h1>
        <span className="grow" />
        <input
          aria-label="Person"
          placeholder="@handle"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        <select aria-label="Window" value={windowSel} onChange={(e) => setWindow(e.target.value)}>
          {["7d", "14d", "30d", "90d"].map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <Button onClick={exportCsv} disabled={!rows.length}>
          Export for 1:1
        </Button>
      </div>
      {rows.length === 0 ? (
        <Empty>No habits recorded for this window.</Empty>
      ) : (
        bySubject.map((g) => (
          <section key={g.subject} aria-label={`Habits of ${g.subject}`}>
            <h2>{g.subject}</h2>
            <table>
              <thead>
                <tr>
                  <th>Habit</th>
                  <th>Count</th>
                  <th>Rate</th>
                  <th>Trend</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {g.habits
                  .filter((h) => !wellbeing.has(h.key))
                  .map((h) => (
                    <tr key={h.id} className={`pol-${h.polarity}`}>
                      <td>
                        <button className="linklike" onClick={() => setOpen(h)}>
                          {(defs.data ?? []).find((d) => d.key === h.key)?.name ?? h.key}
                        </button>{" "}
                        <small>{h.polarity}</small>
                      </td>
                      <td>{h.occurrences}</td>
                      <td>{Number(h.rate).toFixed(2)}</td>
                      <td aria-label={h.trend}>{trendGlyph(h.trend)}</td>
                      <td>{h.status.replace("_", " ")}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {g.habits.some((h) => wellbeing.has(h.key)) && (
              <details>
                <summary>Wellbeing</summary>
                <ul className="plain">
                  {g.habits
                    .filter((h) => wellbeing.has(h.key))
                    .map((h) => (
                      <li key={h.id}>
                        <button className="linklike" onClick={() => setOpen(h)}>
                          {h.key}
                        </button>{" "}
                        {trendGlyph(h.trend)} · {h.status.replace("_", " ")}
                      </li>
                    ))}
                </ul>
              </details>
            )}
          </section>
        ))
      )}
      {restricted && (
        <details>
          <summary>Wellbeing</summary>
          <p role="note">
            Restricted: no wellbeing habits are visible to you. They are shown only to the person
            and their line managers.
          </p>
        </details>
      )}
      <Drawer
        open={!!open}
        onClose={() => setOpen(null)}
        title={open ? `${open.subject} · ${open.key}` : ""}
      >
        {open && (
          <div className="gov-drawer">
            <p>
              {open.occurrences} in {open.window} · rate {Number(open.rate).toFixed(2)} ·{" "}
              {trendGlyph(open.trend)} {open.trend}
            </p>
            <label>
              Status
              <select
                value={open.status}
                disabled={!can["habit.coach"] || open.subject_id === me?.person_id}
                onChange={(e) => patch.mutate({ id: open.id, body: { status: e.target.value } })}
              >
                {HABIT_FLOW.map((s) => (
                  <option key={s} value={s}>
                    {s.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            {can["habit.coach"] && open.subject_id !== me?.person_id && (
              <label>
                Coaching note (managers only; {open.subject} cannot see it)
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  onBlur={() =>
                    note !== (open.coaching_note ?? "") &&
                    patch.mutate({ id: open.id, body: { coaching_note: note } })
                  }
                />
              </label>
            )}
            <h3>Linked violations</h3>
            {linked.length === 0 ? (
              <small>None in the current list.</small>
            ) : (
              <ul className="plain">
                {linked.map((v) => (
                  <li key={v.id}>
                    {v.policy} · {v.evidence?.item ?? v.target_id} · {v.status}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}
