import { useCallback, useEffect, useRef, useState } from "react";
import { addDays, dayLong, hm, ymd } from "./format";
import type { RowActions } from "./ItemRow";
import { CalendarSettingsDialog } from "./CalendarSettings";
import { Month } from "./Month";
import { QuickAdd } from "./QuickAdd";
import { EndOfDayCard, WeeklyReviewCard } from "./Reviews";
import { KeyHelp, PrivacyDialog, RecurringEditor } from "./Settings";
import { Today } from "./Today";
import { Tray } from "./Tray";
import {
  BridgeError,
  type DayView,
  type EndOfDay,
  type ItemRow,
  type MonthView,
  type PersonalBridge,
  type PrivacyInfo,
  type TrayEntry,
  type WeekView,
  type WeeklyReview,
} from "./types";
import { Week, wallStart } from "./Week";

type View = "today" | "week" | "month";
type DialogState =
  | { kind: "eod"; data: EndOfDay }
  | { kind: "weekly"; data: WeeklyReview }
  | { kind: "recur" }
  | { kind: "privacy" }
  | { kind: "calendars" }
  | { kind: "help" }
  | null;

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement &&
  !!t.closest("input, textarea, select, [contenteditable='true']");
const isControl = (t: EventTarget | null) =>
  t instanceof HTMLElement && !!t.closest("button, a, summary");

/** The personal tracker shell: Today / Week / Month, quick add, the ready-to-send tray, the timer and the review cards. It owns no data:
 * every answer comes from the bridge (the Python sidecar), every change goes back through it.
 * Keys: N new · Space timer · C done · X skip · S send · D/W/M views · E end of day · R weekly review · ? help. */
export function PersonalApp({
  bridge,
  pollMs = 30000,
}: {
  bridge: PersonalBridge;
  pollMs?: number;
}) {
  const [view, setView] = useState<View>("today");
  const [anchor, setAnchor] = useState<string | null>(null);
  const [todayIso, setTodayIso] = useState<string>("");
  const [day, setDay] = useState<DayView | null>(null);
  const [week, setWeek] = useState<WeekView | null>(null);
  const [month, setMonth] = useState<MonthView | null>(null);
  const [tray, setTray] = useState<TrayEntry[]>([]);
  const [privacy, setPrivacy] = useState<PrivacyInfo | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState<{
    text: string;
    error: boolean;
  } | null>(null);
  const [undo, setUndo] = useState<{ id: string; label: string } | null>(null);
  const quick = useRef<HTMLInputElement>(null);
  const main = useRef<HTMLElement>(null);

  const announce = useCallback(
    (text: string, error = false) => setMessage({ text, error }),
    [],
  );
  const fail = useCallback(
    (e: unknown) => announce(e instanceof Error ? e.message : String(e), true),
    [announce],
  );

  const load = useCallback(
    async (v: View = view, a: string | null = anchor) => {
      try {
        if (v === "today") {
          const d = await bridge.day(a ?? undefined);
          setDay(d);
          if (!a) setTodayIso((t) => t || d.date);
          setAnchor((x) => x ?? d.date);
        } else if (v === "week") {
          setWeek(await bridge.week(a ?? undefined));
        } else {
          const { y, m } = ymd(a ?? todayIso ?? "2000-01-01");
          setMonth(await bridge.month(a ? y : undefined, a ? m : undefined));
        }
        setTray((await bridge.tray()).entries);
      } catch (e) {
        fail(e);
      }
    },
    [bridge, view, anchor, todayIso, fail],
  );

  useEffect(() => {
    void bridge.privacy().then(setPrivacy).catch(fail);
    void bridge
      .day()
      .then((d) => {
        setTodayIso(d.date);
        setAnchor(d.date);
      })
      .catch(fail);
  }, [bridge, fail]);
  useEffect(() => {
    if (anchor) void load(view, anchor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, anchor]);
  useEffect(() => {
    if (!pollMs) return;
    const t = setInterval(() => void load(), pollMs);
    return () => clearInterval(t);
  }, [pollMs, load]);

  const go = (v: View) => setView(v);
  const openDay = (date: string) => {
    setAnchor(date);
    setView("today");
  };
  const shift = (dir: -1 | 1) => {
    if (!anchor) return;
    if (view === "today") setAnchor(addDays(anchor, dir));
    else if (view === "week") setAnchor(addDays(anchor, 7 * dir));
    else {
      const { y, m } = ymd(anchor);
      setAnchor(
        new Date(Date.UTC(y, m - 1 + dir, 1)).toISOString().slice(0, 10),
      );
    }
  };

  // ------------------------------------------------------------------ actions
  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      if (ok) announce(ok);
      await load();
    } catch (e) {
      fail(e);
    }
  };
  const sendTray = async (
    e: TrayEntry,
    opts: { project?: string; note?: string; minutes?: number } = {},
  ) => {
    try {
      const r = await bridge.send({ id: e.id, ...opts });
      announce(
        `Sent ${hm(r.minutes)} ${e.title} → ${r.project}${r.tags.includes("development") ? " · #development" : ""}`,
      );
      setUndo({ id: e.id, label: e.title });
      await load();
    } catch (err) {
      if (err instanceof BridgeError && err.code === "needs_project")
        announce(
          `${e.title}: choose a project (${err.alternatives.join(", ")})`,
          true,
        );
      else fail(err);
    }
  };
  const rowActions: RowActions = {
    onSelect: setSelected,
    onTimer: (r) =>
      void act(
        async () =>
          r.timer === "running"
            ? bridge.timerStop()
            : bridge.timerStart({ id: r.id }),
        r.timer === "running"
          ? `Stopped ${r.title}`
          : `Timer started: ${r.title}`,
      ),
    onDone: (r) => void act(() => bridge.complete(r.id), `${r.title} done`),
    onSkip: (r) => void act(() => bridge.skip(r.id), `${r.title} skipped`),
    onReopen: (r) => void act(() => bridge.reopen(r.id), `${r.title} reopened`),
    onSend: (r) => {
      const entry = tray.find((t) => t.id === r.id);
      if (entry) void sendTray(entry);
      else
        void act(async () => {
          const res = await bridge.send({ id: r.id });
          setUndo({ id: r.id, label: r.title });
          announce(`Sent ${hm(res.minutes)} ${r.title} → ${res.project}`);
        });
    },
  };
  const add = async (line: string) => {
    try {
      const r = await bridge.add(line);
      announce(`Added ${r.items.map((i) => i.title).join(", ")}`);
      await load();
    } catch (e) {
      fail(e);
    }
  };
  const move = (row: ItemRow, date: string) =>
    void act(
      () => bridge.reschedule(row.id, wallStart(row, date)),
      `${row.title} moved to ${dayLong(date)}`,
    );

  const openEod = async () => {
    try {
      setDialog({ kind: "eod", data: await bridge.eod(anchor ?? undefined) });
    } catch (e) {
      fail(e);
    }
  };
  const openWeekly = async () => {
    try {
      setDialog({
        kind: "weekly",
        data: await bridge.weeklyReview(anchor ?? undefined),
      });
    } catch (e) {
      fail(e);
    }
  };

  // ------------------------------------------------------------------ keyboard
  const allRows = (): ItemRow[] =>
    day ? [...day.timeline, ...day.anytime] : [];
  const onKeyDown = (e: KeyboardEvent) => {
    if (dialog || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const rows = Array.from(
        main.current?.querySelectorAll<HTMLElement>("[data-row]") ?? [],
      );
      const cur = (e.target as HTMLElement | null)?.closest<HTMLElement>(
        "[data-row]",
      );
      if (!rows.length || (!cur && isTyping(e.target))) return;
      if (isControl(e.target) && !cur) return;
      const i = cur ? rows.indexOf(cur) : -1;
      const next =
        rows[
          Math.max(
            0,
            Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)),
          )
        ];
      if (next && !isTyping(e.target)) {
        e.preventDefault();
        next.focus();
      }
      return;
    }
    if (isTyping(e.target)) return;
    const key = e.key.toLowerCase();
    const onRow = (e.target as HTMLElement | null)?.closest?.("[data-row]");
    if (key === " " && isControl(e.target)) return; // Space on a button presses the button
    if (key === " " || key === "c" || key === "x") {
      const row = allRows().find((r) => r.id === selected);
      if (!row || (isControl(e.target) && key !== " ")) return;
      e.preventDefault();
      if (key === " " && row.kind !== "meeting") rowActions.onTimer(row);
      else if (key === "c" && row.kind !== "meeting") rowActions.onDone(row);
      else if (key === "x" && row.kind !== "meeting") rowActions.onSkip(row);
      return;
    }
    if (isControl(e.target) && !onRow) return;
    switch (key) {
      case "n":
        e.preventDefault();
        quick.current?.focus();
        break;
      case "d":
        go("today");
        break;
      case "w":
        go("week");
        break;
      case "m":
        go("month");
        break;
      case "s": {
        const entry = tray.find((t) => t.id === selected);
        const row = allRows().find((r) => r.id === selected);
        if (entry) void sendTray(entry);
        else if (row?.sendable && row.status === "done")
          rowActions.onSend?.(row);
        else
          announce(
            "Select a ready-to-send entry first (Tab or the arrow keys), then press S",
            true,
          );
        break;
      }
      case "e":
        void openEod();
        break;
      case "r":
        void openWeekly();
        break;
      case "?":
        setDialog({ kind: "help" });
        break;
      default:
    }
  };
  const handler = useRef(onKeyDown);
  handler.current = onKeyDown;
  useEffect(() => {
    const h = (e: KeyboardEvent) => handler.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const timerRow = day?.timer
    ? allRows().find((r) => r.id === day.timer?.item_id)
    : undefined;
  const firstRun = privacy !== null && !privacy.first_run_done;

  return (
    <div className="pt">
      <header className="pt-head">
        <h1>My time</h1>
        <nav aria-label="Views">
          {(["today", "week", "month"] as View[]).map((v) => (
            <button
              key={v}
              type="button"
              aria-current={view === v ? "page" : undefined}
              onClick={() => go(v)}
            >
              {v === "today" ? "Today" : v === "week" ? "Week" : "Month"}
              <span className="sr-only">
                {" "}
                view (key {v === "today" ? "D" : v === "week" ? "W" : "M"})
              </span>
            </button>
          ))}
        </nav>
        <div
          role="group"
          aria-label="Move in time"
          style={{ display: "flex", gap: 6 }}
        >
          <button type="button" onClick={() => shift(-1)} aria-label="Previous">
            ‹
          </button>
          <button
            type="button"
            onClick={() => anchor !== todayIso && setAnchor(todayIso)}
            disabled={anchor === todayIso}
          >
            Today<span className="sr-only"> (jump)</span>
          </button>
          <button type="button" onClick={() => shift(1)} aria-label="Next">
            ›
          </button>
        </div>
        <div className="pt-tools">
          <button type="button" onClick={() => setDialog({ kind: "recur" })}>
            Repeat…
          </button>
          <button type="button" onClick={() => void openEod()}>
            End of day
          </button>
          <button type="button" onClick={() => void openWeekly()}>
            Weekly review
          </button>
          <button type="button" onClick={() => setDialog({ kind: "privacy" })}>
            Privacy
          </button>
          <button
            type="button"
            onClick={() => setDialog({ kind: "calendars" })}
          >
            Calendars
          </button>
          <button
            type="button"
            onClick={() => setDialog({ kind: "help" })}
            aria-label="Keyboard shortcuts"
          >
            ?
          </button>
        </div>
        <QuickAdd ref={quick} bridge={bridge} onAdd={add} />
      </header>

      {day?.timer && timerRow ? (
        <div className="pt-timer" role="group" aria-label="Running timer">
          <span aria-hidden="true">▶</span>
          <span>
            <strong>{timerRow.title}</strong> · {hm(day.timer.elapsed_min)} so
            far
          </span>
          <button type="button" onClick={() => rowActions.onTimer(timerRow)}>
            Stop timer
          </button>
        </div>
      ) : null}

      <div
        className={`pt-status ${message?.error ? "pt-error" : ""}`}
        role="status"
        aria-live="polite"
      >
        {message?.text}
        {undo ? (
          <>
            {" "}
            <button
              type="button"
              className="quiet"
              onClick={() => {
                void bridge
                  .unsend(undo.id)
                  .then((r) =>
                    announce(
                      r.how === "discarded"
                        ? `Took back ${undo.label} before it reached the server`
                        : `Correction queued for ${undo.label}`,
                    ),
                  )
                  .then(() => setUndo(null))
                  .then(() => load())
                  .catch(fail);
              }}
            >
              Unsend<span className="sr-only"> {undo.label}</span>
            </button>
          </>
        ) : null}
      </div>

      <main ref={main} id="main" aria-label="My time">
        {view === "today" && day ? (
          <div className="pt-today">
            <Today day={day} selected={selected} actions={rowActions} />
            <Tray
              entries={tray}
              selected={selected}
              actions={{
                onSelect: setSelected,
                onSend: (e, o) => void sendTray(e, o),
                onNotWork: (e) =>
                  void act(
                    () => bridge.notWork(e.id),
                    `${e.title}: not work, won't be offered again`,
                  ),
              }}
            />
          </div>
        ) : null}
        {view === "week" && week ? (
          <>
            <Week
              week={week}
              today={todayIso}
              onMove={move}
              onOpenDay={openDay}
            />
            <div style={{ marginTop: 24 }}>
              <Tray
                entries={tray}
                selected={selected}
                actions={{
                  onSelect: setSelected,
                  onSend: (e, o) => void sendTray(e, o),
                  onNotWork: (e) =>
                    void act(
                      () => bridge.notWork(e.id),
                      `${e.title}: not work`,
                    ),
                }}
              />
            </div>
          </>
        ) : null}
        {view === "month" && month ? (
          <Month month={month} today={todayIso} onOpenDay={openDay} />
        ) : null}
        {!day && !week && !month ? <p className="muted">Loading…</p> : null}
      </main>
      <p className="sr-only">Press ? for keyboard shortcuts.</p>

      {firstRun && privacy ? (
        <PrivacyDialog
          info={privacy}
          firstRun
          bridge={bridge}
          onClose={() => undefined}
          onSaved={setPrivacy}
          announce={announce}
        />
      ) : null}
      {dialog?.kind === "eod" ? (
        <EndOfDayCard
          data={dialog.data}
          bridge={bridge}
          onClose={() => setDialog(null)}
          onChanged={() => void openEod().then(() => load())}
          announce={announce}
        />
      ) : null}
      {dialog?.kind === "weekly" ? (
        <WeeklyReviewCard
          data={dialog.data}
          bridge={bridge}
          onClose={() => setDialog(null)}
          announce={announce}
        />
      ) : null}
      {dialog?.kind === "recur" ? (
        <RecurringEditor
          bridge={bridge}
          onClose={() => setDialog(null)}
          onSaved={() => void load()}
          announce={announce}
        />
      ) : null}
      {dialog?.kind === "privacy" && privacy ? (
        <PrivacyDialog
          info={privacy}
          firstRun={false}
          bridge={bridge}
          onClose={() => setDialog(null)}
          onSaved={setPrivacy}
          announce={announce}
        />
      ) : null}
      {dialog?.kind === "calendars" ? (
        <CalendarSettingsDialog
          bridge={bridge}
          onClose={() => setDialog(null)}
          onChanged={() => void load()}
          announce={announce}
        />
      ) : null}
      {dialog?.kind === "help" ? (
        <KeyHelp onClose={() => setDialog(null)} />
      ) : null}
    </div>
  );
}
