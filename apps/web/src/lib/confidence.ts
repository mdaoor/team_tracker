/** How a confidence value is shown beside a suggestion (UX §3.2): ✓ / 92 % / suggested / ask. */
export type Cue = { kind: "confirmed" | "percent" | "suggested" | "ask"; label: string };

export function confidenceCue(confidence: number | null | undefined, confirmed = false): Cue {
  if (confirmed) return { kind: "confirmed", label: "✓" };
  if (confidence == null) return { kind: "ask", label: "ask" };
  if (confidence >= 0.95) return { kind: "percent", label: `${Math.round(confidence * 100)} %` };
  if (confidence >= 0.7) return { kind: "percent", label: `${Math.round(confidence * 100)} %` };
  if (confidence >= 0.4) return { kind: "suggested", label: "suggested" };
  return { kind: "ask", label: "ask" };
}

export type Severity = "info" | "warning" | "serious" | "critical";
export const severityColor: Record<Severity, string> = {
  info: "var(--s1)",
  warning: "var(--warn)",
  serious: "var(--serious)",
  critical: "var(--crit)",
};

/** Stage age colouring: amber at the expected duration, red at twice it. */
export function stageAgeColor(
  ageDays: number | null | undefined,
  expected: number | null | undefined,
): string {
  if (ageDays == null || !expected) return "var(--text-3)";
  if (ageDays >= expected * 2) return "var(--crit)";
  if (ageDays >= expected) return "var(--warn)";
  return "var(--good)";
}
