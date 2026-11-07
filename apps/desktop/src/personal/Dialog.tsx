import { useEffect, useId, useRef, type ReactNode } from "react";

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A modal with a label, Escape to close, a Tab trap and focus returned to whatever opened it. */
export function Dialog({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(document.activeElement);

  useEffect(() => {
    const before = opener.current as HTMLElement | null;
    const first =
      box.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      box.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => before?.focus?.();
  }, []);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    } else if (e.key === "Tab" && box.current) {
      const items = Array.from(
        box.current.querySelectorAll<HTMLElement>(FOCUSABLE),
      );
      if (!items.length) return;
      const [a, z] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === a) {
        e.preventDefault();
        z.focus();
      } else if (!e.shiftKey && document.activeElement === z) {
        e.preventDefault();
        a.focus();
      }
    }
  };

  return (
    <div className="pt-backdrop" onKeyDown={onKey}>
      <div
        ref={box}
        className="pt-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        style={wide ? { width: "min(860px, 100%)" } : undefined}
      >
        <h2 id={id}>{title}</h2>
        {children}
      </div>
    </div>
  );
}
