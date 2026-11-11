import {
  CATEGORY_GLYPH,
  CATEGORY_LABEL,
  hm,
  range,
  statusLabel,
} from "./format";
import type { ItemRow as Row } from "./types";

export interface RowActions {
  onSelect: (id: string) => void;
  onTimer: (row: Row) => void;
  onDone: (row: Row) => void;
  onSkip: (row: Row) => void;
  onReopen: (row: Row) => void;
  onSend?: (row: Row) => void;
}

/** One planned item. The row itself is focusable (arrow keys move between rows; Space starts/stops its timer, C completes, X skips). */
export function ItemRow({
  row,
  selected,
  actions,
}: {
  row: Row;
  selected: boolean;
  actions: RowActions;
}) {
  const done = row.status === "done";
  const running = row.timer === "running";
  const flags = [
    row.recurring ? "↻ repeats" : "",
    statusLabel(row),
    row.from_calendar
      ? `from ${row.from_calendar === "google" ? "Google Calendar" : "Outlook"}`
      : "",
  ].filter(Boolean);
  return (
    <li
      className={`pt-row cat-${row.category} ${done ? "done" : ""} ${row.status === "skipped" ? "skipped" : ""}`}
      data-row
      data-item-id={row.id}
      data-selected={selected}
      tabIndex={0}
      aria-label={`${row.title}, ${range(row)}, ${CATEGORY_LABEL[row.category]}${flags.length ? ", " + flags.join(", ") : ""}`}
      onFocus={() => actions.onSelect(row.id)}
      onClick={() => actions.onSelect(row.id)}
    >
      <span className="when">{range(row)}</span>
      <span className="title">{row.title}</span>
      <span className="meta">
        <span className={`cat-chip ${row.category}`}>
          <span aria-hidden="true">{CATEGORY_GLYPH[row.category]} </span>
          {CATEGORY_LABEL[row.category]}
        </span>
        {row.duration_min ? <span>{hm(row.duration_min)}</span> : null}
        {row.recurring ? <span title="repeats">↻ repeats</span> : null}
        {running ? <span>▶ {hm(row.elapsed_min)} so far</span> : null}
        {row.sent ? <span>sent</span> : null}
        {row.from_calendar ? (
          <span>
            {row.from_calendar === "google" ? "Google Calendar" : "Outlook"}
          </span>
        ) : null}
        {row.online_meeting_url ? (
          <a
            href={row.online_meeting_url}
            target="_blank"
            rel="noreferrer noopener"
          >
            Join<span className="sr-only"> {row.title} online</span>
          </a>
        ) : null}
        {row.attendees.length ? (
          <span>{row.attendees.length} invited</span>
        ) : null}
      </span>
      <span className="actions">
        {row.kind !== "meeting" && !done ? (
          <button
            type="button"
            onClick={() => actions.onTimer(row)}
            aria-pressed={running}
          >
            {running ? "Stop timer" : "Start timer"}
            <span className="sr-only"> for {row.title}</span>
          </button>
        ) : null}
        {!done && row.kind !== "meeting" ? (
          <button type="button" onClick={() => actions.onDone(row)}>
            Done<span className="sr-only"> {row.title}</span>
          </button>
        ) : null}
        {done ? (
          <button
            type="button"
            className="quiet"
            onClick={() => actions.onReopen(row)}
          >
            Reopen<span className="sr-only"> {row.title}</span>
          </button>
        ) : null}
        {!done && row.status !== "skipped" && row.kind !== "meeting" ? (
          <button
            type="button"
            className="quiet"
            onClick={() => actions.onSkip(row)}
          >
            Skip<span className="sr-only"> {row.title}</span>
          </button>
        ) : null}
        {done && row.sendable && !row.sent && actions.onSend ? (
          <button type="button" onClick={() => actions.onSend?.(row)}>
            Send<span className="sr-only"> {row.title}</span>
          </button>
        ) : null}
      </span>
    </li>
  );
}
