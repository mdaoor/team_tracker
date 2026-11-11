import { forwardRef, useEffect, useRef, useState } from "react";
import { CATEGORY_LABEL, hm } from "./format";
import type { Draft, PersonalBridge } from "./types";

export const PREVIEW_MS = 200;
export const EXAMPLES = [
  "run 30m at 6pm",
  "course: Rust ch.3 1h",
  "every weekday 7:00 run 30m",
  "9–10 reading",
];

function summary(d: Draft): string {
  const when =
    d.rrule_text ??
    (d.all_day
      ? "anytime today"
      : d.start
        ? d.start.slice(0, 16).replace("T", " ")
        : "");
  return `${d.title} · ${CATEGORY_LABEL[d.category]} · ${when}${d.duration_min ? ` · ${hm(d.duration_min)}` : ""}`;
}

/** One line at the top (N focuses it). The preview is the local parser's answer, shown while typing; Enter adds without waiting for it. */
export const QuickAdd = forwardRef<
  HTMLInputElement,
  { bridge: PersonalBridge; onAdd: (line: string) => Promise<void> }
>(function QuickAdd({ bridge, onAdd }, ref) {
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    if (!text.trim()) {
      setDraft(null);
      return;
    }
    const t = setTimeout(() => {
      void bridge
        .preview(text)
        .then((r) => mine === seq.current && setDraft(r.drafts[0] ?? null))
        .catch(() => undefined);
    }, PREVIEW_MS);
    return () => clearTimeout(t);
  }, [text, bridge]);

  return (
    <form
      className="pt-quick"
      onSubmit={(e) => {
        e.preventDefault();
        const line = text.trim();
        if (!line) return;
        setText("");
        setDraft(null);
        void onAdd(line);
      }}
    >
      <label htmlFor="pt-quick-input" className="sr-only">
        Add an item, one line
      </label>
      <input
        id="pt-quick-input"
        ref={ref}
        type="text"
        value={text}
        autoComplete="off"
        placeholder={`Add: ${EXAMPLES[0]}  ·  ${EXAMPLES[1]}  ·  ${EXAMPLES[2]}`}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setText("");
            (e.target as HTMLElement).blur();
          }
        }}
      />
      <div className="pt-preview muted" role="status" aria-live="polite">
        {draft ? `→ ${summary(draft)}` : ""}
        {draft?.suggested_category
          ? ` · looks like ${draft.suggested_category}? Change it after adding.`
          : ""}
      </div>
    </form>
  );
});
