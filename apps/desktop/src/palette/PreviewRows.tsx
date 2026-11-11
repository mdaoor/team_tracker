import type { PreviewCard } from "./types";

/** One block per message: Task / Project / Time / Plan / Tags / Stage, each with its confidence cue (✓ matched, 72 %, suggested, ask). */
export function PreviewRows({
  cards,
  pending,
}: {
  cards: PreviewCard[];
  pending?: boolean;
}) {
  return (
    <div aria-label="Preview" aria-busy={pending}>
      {cards.map((c, i) => (
        <dl key={i} className="card">
          <dt className="action">{c.action.replace("_", " ")}</dt>
          {c.rows.map((r) => (
            <div key={r.label} className="row">
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
              {r.cue ? (
                <small className={`cue ${r.cue.startsWith("✓") ? "ok" : ""}`}>
                  {r.cue}
                </small>
              ) : null}
            </div>
          ))}
        </dl>
      ))}
      {pending ? <p className="spinner">checking with the model…</p> : null}
    </div>
  );
}
