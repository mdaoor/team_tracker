import type { RoleMode } from "./types";

/** The role switch in the palette header. It lists only the modes `/me` allows; it is a lens, never a permission. */
export function RoleSwitch({
  modes,
  role,
  onChange,
}: {
  modes: RoleMode[];
  role: string;
  onChange: (mode: string) => void;
}) {
  if (modes.length <= 1) return null;
  return (
    <label>
      Role mode{" "}
      <select value={role} onChange={(e) => onChange(e.target.value)}>
        {modes.map((m) => (
          <option key={m.mode} value={m.mode}>
            {m.label}
          </option>
        ))}
      </select>
    </label>
  );
}
