import type { Chip } from "./types";

/** One-tap verbs for the current role mode; a tap prefills the palette, it never sends. */
export function VerbChips({
  chips,
  onPick,
}: {
  chips: Chip[];
  onPick: (chip: Chip) => void;
}) {
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Verbs" className="plain chips">
      {chips.map((c) => (
        <li key={c.op + c.label}>
          <button type="button" onClick={() => onPick(c)}>
            {c.label}
          </button>
        </li>
      ))}
    </ul>
  );
}
