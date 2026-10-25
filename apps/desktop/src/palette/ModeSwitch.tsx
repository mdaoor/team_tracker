export type Mode = "assisted" | "manual";

export function ModeSwitch({
  mode,
  onChange,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
}) {
  return (
    <div role="group" aria-label="Mode">
      {(["assisted", "manual"] as const).map((m) => (
        <button key={m} aria-pressed={mode === m} onClick={() => onChange(m)}>
          {m === "assisted" ? "Assisted" : "Manual form"}
        </button>
      ))}
    </div>
  );
}
