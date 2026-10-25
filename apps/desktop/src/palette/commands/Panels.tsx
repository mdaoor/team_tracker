import type { QueueRow, TeamActivity } from "./types";

/** "My queue": review rows with the server's suggested default one click away (Phase 13 R6.1). */
export function QueuePanel({
  rows,
  onResolve,
}: {
  rows: QueueRow[];
  onResolve: (picks: { id: string; choice: string }[]) => void;
}) {
  const withDefault = rows.filter((r) => r.default_choice);
  return (
    <section aria-label="My queue">
      <h2>My queue</h2>
      {rows.length === 0 ? (
        <p>Your review queue is empty.</p>
      ) : (
        <>
          <ul className="plain">
            {rows.map((r) => (
              <li key={r.id}>
                {r.id} · {r.summary ?? r.title ?? ""}{" "}
                {r.default_choice ? (
                  <button
                    aria-label={`Accept ${r.default_choice} for ${r.id}`}
                    onClick={() =>
                      onResolve([{ id: r.id, choice: r.default_choice! }])
                    }
                  >
                    Accept {r.default_choice}
                  </button>
                ) : (
                  <small>no default: use the web console</small>
                )}
              </li>
            ))}
          </ul>
          {withDefault.length > 1 ? (
            <button
              onClick={() =>
                onResolve(
                  withDefault.map((r) => ({
                    id: r.id,
                    choice: r.default_choice!,
                  })),
                )
              }
            >
              Accept all {withDefault.length} defaults
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

/** "Team now": what my reports are working on right now. */
export function TeamNowPanel({ activities }: { activities: TeamActivity[] }) {
  const who = (a: TeamActivity) =>
    typeof a.person === "object"
      ? (a.person?.handle ?? "?")
      : (a.person ?? "?");
  return (
    <section aria-label="Team now">
      <h2>Team now</h2>
      {activities.length === 0 ? (
        <p>Nobody on your teams has an active task.</p>
      ) : (
        <ul className="plain">
          {activities.map((a, i) => (
            <li key={i}>
              {who(a)} · {a.item?.key ?? a.key ?? ""}{" "}
              {a.item?.title ?? a.title ?? ""}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
