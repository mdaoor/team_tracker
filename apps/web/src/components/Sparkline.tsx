import { sparkPoints } from "../lib/governance";

/** Tiny inline trend. The numbers are in the accessible label, so the picture is never the only carrier. */
export function Sparkline({
  values,
  label,
  w = 80,
  h = 20,
}: {
  values: (number | null)[];
  label: string;
  w?: number;
  h?: number;
}) {
  const { segments, last } = sparkPoints(values, w, h);
  const text = values.map((v) => (v == null ? "n/a" : v.toFixed(2))).join(", ");
  return (
    <svg role="img" aria-label={`${label}: ${text}`} width={w} height={h} className="spark">
      {segments.map((p, i) =>
        p.includes(" ") ? (
          <polyline key={i} points={p} fill="none" stroke="currentColor" strokeWidth={1.5} />
        ) : (
          <circle
            key={i}
            cx={Number(p.split(",")[0])}
            cy={Number(p.split(",")[1])}
            r={1.5}
            fill="currentColor"
          />
        ),
      )}
      {last && <circle cx={last[0]} cy={last[1]} r={2} fill="currentColor" />}
    </svg>
  );
}
