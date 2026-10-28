import { useState } from "react";
import {
  CATEGORY_GLYPH,
  CATEGORY_LABEL,
  clock,
  dayLabel,
  hm,
  range,
} from "./format";
import { Meter } from "./Today";
import type { ItemRow, WeekView } from "./types";

/** Seven days with a capacity bar each. Drag an item onto another day to reschedule it (same time of day); the "Move to" select does the
 * same from the keyboard. Recurring instances carry a ↻. Moving or completing an instance never touches the server. */
export function Week({
  week,
  today,
  onMove,
  onOpenDay,
}: {
  week: WeekView;
  today: string;
  onMove: (row: ItemRow, date: string) => void;
  onOpenDay: (date: string) => void;
}) {
  const [over, setOver] = useState<string | null>(null);
  return (
    <section aria-labelledby="week-h">
      <h2 id="week-h">
        Week of {dayLabel(week.week_start)}{" "}
        <span className="muted">
          · planned {hm(week.planned_min)} of {hm(week.capacity_min)}
        </span>
      </h2>
      <div className="pt-week">
        {week.days.map((d) => (
          <section
            key={d.date}
            className={`pt-day ${d.date === today ? "today" : ""}`}
            aria-labelledby={`day-${d.date}`}
            data-over={over === d.date}
            data-day={d.date}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(d.date);
            }}
            onDragLeave={() => setOver((o) => (o === d.date ? null : o))}
            onDrop={(e) => {
              e.preventDefault();
              setOver(null);
              const id = e.dataTransfer.getData("text/plain");
              const row = week.days
                .flatMap((x) => [...x.timeline, ...x.anytime])
                .find((r) => r.id === id);
              if (row) onMove(row, d.date);
            }}
          >
            <h3 id={`day-${d.date}`}>
              <button
                type="button"
                className="quiet"
                onClick={() => onOpenDay(d.date)}
                style={{ border: 0, padding: 0, minHeight: 0, fontWeight: 700 }}
              >
                {dayLabel(d.date)}
                <span className="sr-only"> (open the day)</span>
              </button>
              <span className="muted">{hm(d.planned_min)}</span>
            </h3>
            <Meter
              value={d.planned_min}
              max={d.capacity_min}
              label={`${dayLabel(d.date)} load`}
            />
            {[...d.timeline, ...d.anytime].map((r) => (
              <div
                key={r.id}
                className={`pt-chip-item cat-${r.category}`}
                draggable
                data-item-id={r.id}
                onDragStart={(e) => e.dataTransfer.setData("text/plain", r.id)}
              >
                <span>
                  <strong>{r.title}</strong>
                  {r.recurring ? (
                    <span title="repeats">
                      {" "}
                      <span aria-hidden="true">↻</span>
                      <span className="sr-only"> repeats</span>
                    </span>
                  ) : null}
                </span>
                <span className="muted">
                  <span aria-hidden="true">{CATEGORY_GLYPH[r.category]} </span>
                  {CATEGORY_LABEL[r.category]} · {range(r)}
                  {r.status === "done"
                    ? " · done"
                    : r.status === "skipped"
                      ? " · skipped"
                      : ""}
                </span>
                {r.from_calendar ? null : (
                  <label>
                    <span className="sr-only">Move {r.title} to</span>
                    <select
                      aria-label={`Move ${r.title} to`}
                      value=""
                      onChange={(e) =>
                        e.target.value && onMove(r, e.target.value)
                      }
                    >
                      <option value="">Move to…</option>
                      {week.days
                        .filter((x) => x.date !== d.date)
                        .map((x) => (
                          <option key={x.date} value={x.date}>
                            {dayLabel(x.date)}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
              </div>
            ))}
            {d.timeline.length + d.anytime.length === 0 ? (
              <p className="muted">Free</p>
            ) : null}
          </section>
        ))}
      </div>
      <p className="pt-totals muted">
        {(Object.keys(week.totals) as (keyof typeof week.totals)[]).map((c) => (
          <span key={c}>
            {CATEGORY_LABEL[c]} {hm(week.totals[c].planned)}
          </span>
        ))}
      </p>
    </section>
  );
}

export const wallStart = (row: ItemRow, date: string): string => {
  const t = row.start ? clock(row.start) : "09:00";
  const off = row.start ? row.start.slice(19) : "+03:00";
  return `${date}T${t}:00${off || "+03:00"}`;
};
