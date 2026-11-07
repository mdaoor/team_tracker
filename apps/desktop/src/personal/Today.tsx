import { dayLong, hm } from "./format";
import { ItemRow, type RowActions } from "./ItemRow";
import type { DayView } from "./types";

export function Meter({
  value,
  max,
  label,
}: {
  value: number;
  max: number;
  label: string;
}) {
  const pct = max
    ? Math.min(100, Math.round((100 * value) / max))
    : value
      ? 100
      : 0;
  return (
    <div
      className={`pt-meter ${max && value > max ? "over" : ""}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={Math.max(max, 1)}
      aria-valuenow={Math.min(value, Math.max(max, 1))}
      aria-valuetext={`${hm(value)} of ${hm(max)}`}
    >
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

/** The day: planned vs available hours, the timeline, items for "anytime" and the server items assigned to me (shown distinct). */
export function Today({
  day,
  selected,
  actions,
}: {
  day: DayView;
  selected: string | null;
  actions: RowActions;
}) {
  const empty = day.timeline.length + day.anytime.length === 0;
  return (
    <section aria-labelledby="today-h">
      <h2 id="today-h">{dayLong(day.date)}</h2>
      <div className="pt-summary">
        <span>
          Planned {hm(day.planned_min)} of {hm(day.capacity_min)}
          {day.capacity_min ? ` (${day.load_pct} %)` : ""}
        </span>
        <Meter
          value={day.planned_min}
          max={day.capacity_min}
          label="Planned time against available time"
        />
      </div>
      {empty ? (
        <p className="muted">
          Nothing planned. Press N and type a line like “run 30m at 6pm”.
        </p>
      ) : null}
      {day.timeline.length ? (
        <>
          <h3 className="sr-only">Timeline</h3>
          <ul className="pt-list" data-list="timeline">
            {day.timeline.map((r) => (
              <ItemRow
                key={r.id}
                row={r}
                selected={selected === r.id}
                actions={actions}
              />
            ))}
          </ul>
        </>
      ) : null}
      {day.anytime.length ? (
        <>
          <h3 style={{ margin: "16px 0 8px" }}>Anytime today</h3>
          <ul className="pt-list" data-list="anytime">
            {day.anytime.map((r) => (
              <ItemRow
                key={r.id}
                row={r}
                selected={selected === r.id}
                actions={actions}
              />
            ))}
          </ul>
        </>
      ) : null}
      {day.server_items.length ? (
        <>
          <h3 style={{ margin: "16px 0 8px" }}>
            Assigned to me in Team Tracker
          </h3>
          <ul className="pt-list" data-list="server">
            {day.server_items.map((s) => (
              <li key={s.key} className="pt-row cat-work">
                <span className="when">{s.project}</span>
                <span className="title">
                  {s.key} {s.title}
                </span>
                <span className="meta">
                  <span>from the server</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
