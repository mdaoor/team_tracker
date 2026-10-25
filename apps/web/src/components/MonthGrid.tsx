import { useState, type ReactNode } from "react";

export type DayEvent = { kind: string; label: string; title?: string };
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** A month calendar with previous / next / today navigation. `events` maps ISO dates to what happens that day. */
export function MonthGrid({
  events,
  label = "Calendar",
  render,
}: {
  events: Record<string, DayEvent[]>;
  label?: string;
  render?: (e: DayEvent) => ReactNode;
}) {
  const [offset, setOffset] = useState(0);
  const now = new Date();
  const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offset, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const title = first.toLocaleString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const cells: (string | null)[] = [
    ...Array((first.getUTCDay() + 6) % 7).fill(null),
    ...Array.from({ length: last.getUTCDate() }, (_, i) =>
      iso(new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), i + 1))),
    ),
  ];
  return (
    <div>
      <div className="row" role="group" aria-label={`${label} month`}>
        <button className="btn" aria-label="Previous month" onClick={() => setOffset((o) => o - 1)}>
          ‹
        </button>
        <strong aria-live="polite">{title}</strong>
        <button className="btn" aria-label="Next month" onClick={() => setOffset((o) => o + 1)}>
          ›
        </button>
        <button className="btn btn-ghost" disabled={offset === 0} onClick={() => setOffset(0)}>
          Today
        </button>
      </div>
      <div className="vcal" role="group" aria-label={`${label}: ${title}`}>
        {WEEKDAYS.map((d) => (
          <div key={d} className="vcal-h" aria-hidden>
            {d}
          </div>
        ))}
        {cells.map((d, i) => (
          <div key={i} data-day={d ?? ""} className={d === iso(now) ? "today" : ""}>
            {d && <strong>{d.slice(8)}</strong>}
            {(d ? (events[d] ?? []) : []).map((e, j) => (
              <div key={j} className={`ev ${e.kind}`} title={e.title ?? e.label}>
                {render ? render(e) : e.label}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Spread items that span dates (iterations) over every day they cover. */
export function spanDays(start: string, end: string): string[] {
  const out: string[] = [];
  for (let d = new Date(start + "T00:00:00Z"); d <= new Date(end + "T00:00:00Z") && out.length < 120; d.setUTCDate(d.getUTCDate() + 1))
    out.push(d.toISOString().slice(0, 10));
  return out;
}
