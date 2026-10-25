import { CATEGORIES, type Category, type MonthView } from "./types";
import {
  CATEGORY_GLYPH,
  CATEGORY_LABEL,
  dayLabel,
  hm,
  monthLabel,
  ymd,
} from "./format";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function cellLabel(
  date: string,
  d: MonthView["days"][string] | undefined,
): string {
  if (!d) return `${dayLabel(date)}: nothing planned`;
  const parts = (Object.keys(d.minutes) as Category[]).map(
    (c) => `${CATEGORY_LABEL[c]} ${hm(d.minutes[c])}`,
  );
  return `${dayLabel(date)}: ${parts.join(", ") || "nothing planned"}${d.sent_min ? `; sent ${hm(d.sent_min)}` : ""}`;
}

/** Calendar grid with a dot per category (glyph + colour), the month's hours and a sent-vs-private sparkline. */
export function Month({
  month,
  today,
  onOpenDay,
}: {
  month: MonthView;
  today: string;
  onOpenDay: (date: string) => void;
}) {
  const peak = Math.max(1, ...month.sparkline.map((s) => s.sent + s.private));
  const sent = month.sparkline.reduce((a, s) => a + s.sent, 0);
  const priv = month.sparkline.reduce((a, s) => a + s.private, 0);
  const w = 8;
  return (
    <section aria-labelledby="month-h">
      <h2 id="month-h">{monthLabel(month.year, month.month)}</h2>
      <p className="pt-totals">
        <strong>Hours</strong>
        {CATEGORIES.map((c) => (
          <span key={c}>
            <span aria-hidden="true">{CATEGORY_GLYPH[c]} </span>
            {CATEGORY_LABEL[c]} {month.totals_h[c]} h
          </span>
        ))}
      </p>
      <table className="pt-month">
        <caption className="sr-only">
          Days of {monthLabel(month.year, month.month)}; choose a day to open it
        </caption>
        <thead>
          <tr>
            {DOW.map((d) => (
              <th key={d} scope="col">
                {d}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {month.weeks.map((wk) => (
            <tr key={wk[0]}>
              {wk.map((date) => {
                const inMonth = ymd(date).m === month.month;
                const d = month.days[date];
                return (
                  <td
                    key={date}
                    className={`${inMonth ? "" : "out"} ${date === today ? "today" : ""}`}
                  >
                    <button
                      type="button"
                      onClick={() => onOpenDay(date)}
                      aria-label={cellLabel(date, d)}
                      aria-current={date === today ? "date" : undefined}
                    >
                      <span aria-hidden="true">{ymd(date).d}</span>
                      <span className="dots" aria-hidden="true">
                        {(d?.dots ?? []).map((c) => (
                          <span key={c} className={c}>
                            {CATEGORY_GLYPH[c]}
                          </span>
                        ))}
                      </span>
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <h3 style={{ marginTop: 16 }}>Sent and private time</h3>
      <svg
        className="pt-spark"
        viewBox={`0 0 ${Math.max(1, month.sparkline.length) * w} 40`}
        role="img"
        aria-label={`Time per day this month: sent to Team Tracker ${hm(sent)}, private ${hm(priv)}`}
        preserveAspectRatio="none"
      >
        {month.sparkline.map((s, i) => {
          const h1 = (40 * s.private) / peak;
          const h2 = (40 * s.sent) / peak;
          return (
            <g key={s.date}>
              <rect
                x={i * w + 1}
                y={40 - h1}
                width={w - 2}
                height={h1}
                fill="var(--muted)"
              />
              <rect
                x={i * w + 1}
                y={40 - h1 - h2}
                width={w - 2}
                height={h2}
                fill="var(--accent)"
              />
            </g>
          );
        })}
      </svg>
      <p className="muted">
        <span aria-hidden="true" style={{ color: "var(--accent)" }}>
          ■{" "}
        </span>
        sent {hm(sent)} · <span aria-hidden="true">■ </span>private {hm(priv)}
      </p>
    </section>
  );
}
