import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ApiError } from "../api/http";

type Toast = {
  id: number;
  key?: string;
  text: string;
  undo?: () => void;
  actions?: { label: string; run: () => void }[];
  tone: "info" | "error";
  requestId?: string;
};
type Ctx = {
  push: (
    text: string,
    opts?: { undo?: () => void; ms?: number; key?: string; actions?: { label: string; run: () => void }[] },
  ) => void;
  error: (e: unknown) => void;
};
const C = createContext<Ctx>({ push: () => {}, error: () => {} });
export const useToasts = () => useContext(C);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const add = useCallback((t: Omit<Toast, "id">, ms: number) => {
    const id = Date.now() + Math.random();
    // Same key (or identical text) replaces the visible toast instead of stacking a duplicate (UX review 9).
    const key = t.key ?? `${t.tone}:${t.text}`;
    setItems((x) => [...x.filter((i) => (i.key ?? `${i.tone}:${i.text}`) !== key), { ...t, key, id }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), ms);
  }, []);
  const value = useMemo<Ctx>(
    () => ({
      push: (text, o) =>
        add(
          { text, undo: o?.undo, actions: o?.actions, tone: "info", key: o?.key },
          o?.ms ?? (o?.undo || o?.actions ? 10_000 : 4000),
        ),
      error: (e) =>
        add(
          {
            text: e instanceof Error ? e.message : String(e),
            tone: "error",
            requestId: e instanceof ApiError ? e.requestId : undefined,
          },
          8000,
        ),
    }),
    [add],
  );
  return (
    <C.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            {t.text}
            {t.requestId && <small> (request {t.requestId.slice(0, 8)})</small>}
            {t.actions?.map((a) => (
              <button
                key={a.label}
                className="btn btn-ghost"
                onClick={() => {
                  a.run();
                  setItems((x) => x.filter((i) => i.id !== t.id));
                }}
              >
                {a.label}
              </button>
            ))}
            {t.undo && (
              <button
                className="btn btn-ghost"
                onClick={() => {
                  t.undo?.();
                  setItems((x) => x.filter((i) => i.id !== t.id));
                }}
              >
                Undo
              </button>
            )}
          </div>
        ))}
      </div>
    </C.Provider>
  );
}
