import type { Pack } from "./types";

export const ACTIONS = [
  "start", "done", "pause", "resume", "blocked", "note", "create", "log_time", "update",
] as const; // prettier-ignore
export const DURATION_PRESETS = [15, 30, 60, 120] as const;

export type Answers = Record<
  string,
  string | string[] | number | boolean | null | undefined
>;

const present = (v: Answers[string]) =>
  v !== undefined &&
  v !== null &&
  v !== "" &&
  !(Array.isArray(v) && v.length === 0);

/** Mandatory fields for these answers: the project's list if the pack has one, else the workspace list (+ the action rules). The
 * server never enforces this - any valid file is accepted - so it is purely the form's job and works offline. */
export function requiredFor(pack: Pack, a: Answers): string[] {
  const mm = pack.manual_mode ?? {
    required_fields: ["action", "item", "project"],
  };
  const project = typeof a.project === "string" ? a.project : "";
  const req = [...(mm.per_project?.[project] ?? mm.required_fields)];
  if (a.action === "log_time" && !req.includes("minutes")) req.push("minutes");
  if (a.action === "create" && !req.includes("item_type"))
    req.push("item_type");
  return req;
}

export function missingFields(pack: Pack, a: Answers): string[] {
  return requiredFor(pack, a).filter((f) => !present(a[f]));
}

/** "45m", "1h30", "2h", "90" (minutes) -> minutes, or null when it is not a duration. */
export function parseMinutes(text: string): number | null {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  if (/^\d+$/.test(t)) return Number(t);
  const m =
    /^(?:(\d+(?:\.\d+)?)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:ute)?s?)?)?$/.exec(
      t,
    );
  if (!m || (!m[1] && !m[2])) {
    const bare = /^(\d+)h(\d+)$/.exec(t);
    return bare ? Number(bare[1]) * 60 + Number(bare[2]) : null;
  }
  return Math.round(Number(m[1] ?? 0) * 60 + Number(m[2] ?? 0));
}
