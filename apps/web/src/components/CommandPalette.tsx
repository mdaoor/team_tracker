import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

export type Command = { label: string; to: string };

export function CommandPalette({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  const hits = useMemo(
    () => commands.filter((c) => c.label.toLowerCase().includes(q.toLowerCase())).slice(0, 8),
    [commands, q],
  );
  if (!open) return null;
  const go = (to: string) => {
    setOpen(false);
    setQ("");
    nav(to);
  };
  return (
    <div className="palette" role="dialog" aria-label="Command palette">
      <input
        ref={input}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Go to…"
        aria-label="Go to"
        onKeyDown={(e) => e.key === "Enter" && hits[0] && go(hits[0].to)}
      />
      <ul role="listbox">
        {hits.map((c) => (
          <li key={c.to} role="option" aria-selected={false}>
            <button onClick={() => go(c.to)}>{c.label}</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
