import { useEffect, useRef, useState } from "react";
import { PreviewRows } from "./PreviewRows";
import type { Bridge, Parsed } from "./types";

export const DEBOUNCE_MS = 300;
export const GHOSTS = [
  "fix the login redirect loop, took 2h, plan to finish tomorrow 5pm",
  "doing 1. write tests, 2. deploy | est 3h",
  "stuck on db access waiting for ops",
];

/** The single-line palette. Keys: Enter send · Tab edit rows · Esc discard · ⌘J Jira · ⌘M manual · ⌘E open JSON.
 * Parsing: the structured parser's answer is shown at once; the model's replaces it when it arrives. `Enter` never waits. */
export function PaletteInput({
  bridge,
  jiraAvailable,
  onSent,
  onManual,
  onDiscard,
  onEditRows,
}: {
  bridge: Bridge;
  jiraAvailable?: boolean;
  onSent: (r: Awaited<ReturnType<Bridge["send"]>>) => void;
  onManual: (text: string) => void;
  onDiscard: () => void;
  onEditRows?: () => void;
}) {
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [llmPending, setLlmPending] = useState(false);
  const [jira, setJira] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    if (!text.trim()) {
      setParsed(null);
      setLlmPending(false);
      return;
    }
    const mine = ++seq.current;
    const t = setTimeout(() => {
      void bridge
        .parse(text, { llm: false })
        .then((p) => mine === seq.current && setParsed(p));
      setLlmPending(true);
      void bridge
        .parse(text, { llm: true })
        .then((p) => mine === seq.current && setParsed(p))
        .catch(() => undefined)
        .finally(() => mine === seq.current && setLlmPending(false));
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [text, bridge]);

  const send = async () => {
    // Enter never blocks on the model: if the preview is not there yet, parse structurally right now.
    const p = parsed ?? (await bridge.parse(text, { llm: false }));
    const r = await bridge.send(p.batch, { jira });
    setText("");
    onSent(r);
  };

  return (
    <div className="palette">
      <input
        aria-label="What did you do?"
        autoFocus
        value={text}
        placeholder={GHOSTS[0]}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          const meta = e.metaKey || e.ctrlKey;
          if (e.key === "Enter" && text.trim()) {
            e.preventDefault();
            void send();
          } else if (e.key === "Escape") {
            setText("");
            onDiscard();
          } else if (e.key === "Tab" && parsed) {
            e.preventDefault();
            onEditRows?.();
          } else if (meta && e.key.toLowerCase() === "j" && jiraAvailable) {
            e.preventDefault();
            setJira((j) => !j);
          } else if (meta && e.key.toLowerCase() === "m") {
            e.preventDefault();
            onManual(text);
          } else if (meta && e.key.toLowerCase() === "e" && parsed) {
            e.preventDefault();
            void bridge
              .openInEditor(parsed.batch)
              .then((b) => setParsed({ ...parsed, batch: b }));
          }
        }}
      />
      {jiraAvailable ? (
        <span aria-label="Jira chip" className={jira ? "chip on" : "chip"}>
          Jira {jira ? "on" : "off"}
        </span>
      ) : null}
      {parsed ? (
        <PreviewRowsLazy parsed={parsed} pending={llmPending} />
      ) : (
        <GhostExamples />
      )}
    </div>
  );
}

function PreviewRowsLazy({
  parsed,
  pending,
}: {
  parsed: Parsed;
  pending: boolean;
}) {
  return (
    <>
      <PreviewRows cards={parsed.cards} pending={pending} />
      {parsed.source === "structured" && !pending ? (
        <small>Parsed without the model — check the rows.</small>
      ) : null}
    </>
  );
}

function GhostExamples() {
  return (
    <ul aria-label="Examples" className="ghost">
      {GHOSTS.map((g) => (
        <li key={g}>{g}</li>
      ))}
    </ul>
  );
}
