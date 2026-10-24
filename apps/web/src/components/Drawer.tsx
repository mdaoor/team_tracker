import { useEffect, type ReactNode } from "react";

export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <aside className="drawer" role="dialog" aria-label="Item details">
      <header className="drawer-h">
        <h2>{title}</h2>
        <button className="btn btn-ghost" aria-label="Close" onClick={onClose}>
          ✕
        </button>
      </header>
      <div className="drawer-b">{children}</div>
    </aside>
  );
}
