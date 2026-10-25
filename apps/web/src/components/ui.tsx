import type { ButtonHTMLAttributes, ReactNode } from "react";
import { confidenceCue, severityColor, type Severity } from "../lib/confidence";
import { ago } from "../lib/format";

export function Button({
  variant = "default",
  ...p
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "ghost" }) {
  return <button {...p} className={`btn btn-${variant} ${p.className ?? ""}`} />;
}

export function Chip({
  children,
  onRemove,
  title,
}: {
  children: ReactNode;
  onRemove?: () => void;
  title?: string;
}) {
  return (
    <span className="chip" title={title}>
      {children}
      {onRemove && (
        <button aria-label="remove" className="chip-x" onClick={onRemove}>
          ×
        </button>
      )}
    </span>
  );
}

export function Pill({ severity, children }: { severity: Severity; children: ReactNode }) {
  return (
    <span
      className="pill"
      data-severity={severity}
      style={{ borderColor: severityColor[severity], color: severityColor[severity] }}
    >
      {children}
    </span>
  );
}

export function Card({
  title,
  children,
  actions,
}: {
  title?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-h">
          <h3>{title}</h3>
          <div>{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function ConfidenceCue({
  confidence,
  confirmed,
}: {
  confidence?: number | null;
  confirmed?: boolean;
}) {
  const cue = confidenceCue(confidence, confirmed);
  return (
    <span
      className={`cue cue-${cue.kind}`}
      data-cue={cue.kind}
      aria-label={`confidence: ${cue.label}`}
    >
      {cue.label}
    </span>
  );
}

/** "set by · when · inherited from" for any overlay value, from `/items/{key}/provenance`. */
export function ProvenanceChip({
  source,
  setBy,
  setAt,
  inheritedFrom,
}: {
  source: string;
  setBy?: string | null;
  setAt?: string | null;
  inheritedFrom?: string | null;
}) {
  // Visible text answers "who, when, from where" at a glance (UX §3.4); the tooltip repeats it with the source.
  const when = setAt ? ago(setAt) : null;
  const who = setBy ? `set by ${setBy}` : source === "manual" ? "set manually" : `set by ${source}`;
  const short = [who, when, inheritedFrom ? `inherited from ${inheritedFrom}` : null].filter(Boolean).join(" · ");
  return (
    <span className="prov" data-source={source} title={`${short} (${source})`} tabIndex={0}>
      <span aria-hidden>{source === "manual" ? "✎" : source === "inherited" ? "↳" : "ƒ"}</span>{" "}
      <span className="prov-text">{short}</span>
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
