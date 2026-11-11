import type { OutboxEntry } from "./types";

export function OutboxList({
  entries,
  onResend,
  onDiscard,
  onEdit,
}: {
  entries: OutboxEntry[];
  onResend: (id: string) => void;
  onDiscard: (id: string) => void;
  onEdit: (id: string) => void;
}) {
  if (entries.length === 0)
    return <p>Nothing waiting. Everything you wrote has been delivered.</p>;
  return (
    <ul aria-label="Outbox" className="plain">
      {entries.map((e) => (
        <li key={e.id} data-status={e.status}>
          <span className={`pill ${e.status}`}>{e.status}</span> {e.summary}
          {e.jira_key ? <small> · Jira {e.jira_key}</small> : null}
          {e.error ? <small role="alert"> · {e.error}</small> : null}
          {e.status !== "sent" ? (
            <>
              <button onClick={() => onEdit(e.id)}>Edit</button>
              <button onClick={() => onResend(e.id)}>Resend</button>
              <button onClick={() => onDiscard(e.id)}>Delete</button>
            </>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
