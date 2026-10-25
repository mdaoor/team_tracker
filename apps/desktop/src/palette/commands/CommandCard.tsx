import { useEffect, useRef, useState } from "react";
import type { Outcome } from "./types";

const show = (v: unknown): string =>
  Array.isArray(v)
    ? v.join(", ")
    : typeof v === "object" && v !== null
      ? JSON.stringify(v)
      : String(v);

/** One command: its arguments, the "Effect preview" the server computed without writing, and the confirmation.
 * A command the server would reject has no Confirm button. A destructive one needs a second, explicit confirmation. */
export function CommandCard({ outcome }: { outcome: Outcome }) {
  const rows = Object.entries(outcome.args);
  return (
    <article aria-label={`Command ${outcome.op}`} className="card command">
      <h3>
        {outcome.op}
        {outcome.destructive ? <strong> · destructive</strong> : null}
      </h3>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="row">
            <dt>{k.replace(/_/g, " ")}</dt>
            <dd>{show(v)}</dd>
          </div>
        ))}
        {outcome.on_behalf_of ? (
          <div className="row">
            <dt>on behalf of</dt>
            <dd>{outcome.on_behalf_of}</dd>
          </div>
        ) : null}
      </dl>
      <section aria-label="Effect preview">
        <h4>Effect preview</h4>
        {outcome.status === "previewed" ? (
          outcome.rows.length ? (
            <dl>
              {outcome.rows.map((r) => (
                <div key={r.label} className="row">
                  <dt>{r.label}</dt>
                  <dd>{r.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>No visible change.</p>
          )
        ) : outcome.status === "rejected" ? (
          <p role="alert">
            Rejected:{" "}
            {outcome.errors.map((e) => e.message ?? e.code).join("; ")}
          </p>
        ) : outcome.status === "offline" ? (
          <p>Offline: the server will validate this when it is sent.</p>
        ) : (
          <p>This op has no preview.</p>
        )}
      </section>
    </article>
  );
}

/** Confirm / Discard for a group of cards; destructive groups ask a second time before anything is sent. */
export function ConfirmBar({
  outcomes,
  busy,
  onConfirm,
  onDiscard,
}: {
  outcomes: Outcome[];
  busy?: boolean;
  onConfirm: (second: boolean) => void;
  onDiscard: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const second = useRef<HTMLButtonElement>(null);
  const rejected = outcomes.some((o) => o.status === "rejected");
  const destructive = outcomes.some((o) => o.destructive);
  useEffect(() => {
    if (armed) second.current?.focus();
  }, [armed]);
  return (
    <div role="group" aria-label="Confirm">
      {rejected ? (
        <p role="alert">Nothing can be sent: the server would reject this.</p>
      ) : armed ? (
        <div role="alert">
          <p>
            This is destructive and cannot be undone from the client. Really
            send?
          </p>
          <button ref={second} disabled={busy} onClick={() => onConfirm(true)}>
            Yes, do it
          </button>
          <button onClick={() => setArmed(false)}>Cancel</button>
        </div>
      ) : (
        <button
          disabled={busy}
          onClick={() => (destructive ? setArmed(true) : onConfirm(false))}
        >
          Confirm
        </button>
      )}
      <button onClick={onDiscard}>Discard</button>
    </div>
  );
}
