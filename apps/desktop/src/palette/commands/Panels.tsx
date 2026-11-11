import type { QueueRow, TeamActivity } from "./types";

/** "My queue": review rows with the server's suggestion one click (or Enter) away (Phase 13 R6.1). */
export function QueuePanel({
  rows,
  onResolve,
}: {
  rows: QueueRow[];
  onResolve: (
    picks: { ref: string; choice: Record<string, unknown> }[],
  ) => void;
}) {
  const withChoice = rows.filter((r) => r.choice);
  return (
    <section aria-label="My queue">
      <h2>My queue</h2>
      {rows.length === 0 ? (
        <p>Your review queue is empty.</p>
      ) : (
        <>
          <ul className="plain">
            {rows.map((r) => (
              <li key={r.ref}>
                {r.line}{" "}
                {r.choice ? (
                  <button
                    aria-label={`Accept ${r.choice_label} for ${r.item_key}`}
                    onClick={() =>
                      onResolve([{ ref: r.ref, choice: r.choice! }])
                    }
                  >
                    Accept {r.choice_label}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
          {withChoice.length > 1 ? (
            <button
              onClick={() =>
                onResolve(
                  withChoice.map((r) => ({ ref: r.ref, choice: r.choice! })),
                )
              }
            >
              Accept all {withChoice.length} defaults
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

/** "Team now": what my reports are working on right now. */
export function TeamNowPanel({ activities }: { activities: TeamActivity[] }) {
  return (
    <section aria-label="Team now">
      <h2>Team now</h2>
      {activities.length === 0 ? (
        <p>Nobody on your teams has an active task.</p>
      ) : (
        <ul className="plain">
          {activities.map((a, i) => (
            <li key={i}>{a.line}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
