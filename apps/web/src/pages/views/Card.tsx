import { Link, useLocation } from "react-router-dom";
import type { Row } from "../../api/http";
import { stageAgeColor } from "../../lib/confidence";

const PRIO: Record<string, string> = {
  lowest: "var(--text-3)",
  low: "var(--s1)",
  medium: "var(--s4)",
  high: "var(--serious)",
  highest: "var(--crit)",
  critical: "var(--crit)",
};

/** Card anatomy (UX §4.2): key · priority dot · weight · risk chip / title / assignee · stage age · time / tags. */
export function Card({ item }: { item: Row }) {
  const loc = useLocation();
  const sp = new URLSearchParams(loc.search);
  sp.set("item", item.key);
  return (
    <article className="vcard" data-key={item.key}>
      <header>
        <Link className="key" to={{ search: sp.toString() }}>
          {item.key}
        </Link>
        <span className="dot" title={item.priority} style={{ background: PRIO[item.priority] }} />
        <span title="weight">⚖ {item.weight}</span>
        {item.top_risk_score != null && (
          <span className="pill" style={{ borderColor: "var(--serious)" }}>
            risk {item.top_risk_score}
          </span>
        )}
      </header>
      <p>{item.title}</p>
      <footer>
        <span>{item.assignee ?? "unassigned"}</span>
        {item.stage_age_days != null && (
          <span
            title="days in stage"
            style={{ color: stageAgeColor(item.stage_age_days, item.expected_duration_days) }}
          >
            {item.stage_age_days}d
            {item.expected_duration_days ? `/${item.expected_duration_days}` : ""}
          </span>
        )}
        <span>{item.hours_logged} h</span>
        {(item.tags as string[]).map((t) => (
          <small key={t}>#{t}</small>
        ))}
      </footer>
    </article>
  );
}
