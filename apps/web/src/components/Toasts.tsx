import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ApiError } from "../api/http";

type Toast = {
  id: number;
  text: string;
  undo?: () => void;
  tone: "info" | "error";
  requestId?: string;
};
type Ctx = {
  push: (text: string, opts?: { undo?: () => void; ms?: number }) => void;
  error: (e: unknown) => void;
};
const C = createContext<Ctx>({ push: () => {}, error: () => {} });
export const useToasts = () => useContext(C);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const add = useCallback((t: Omit<Toast, "id">, ms: number) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { ...t, id }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), ms);
  }, []);
  const value = useMemo<Ctx>(
    () => ({
      push: (text, o) =>
        add({ text, undo: o?.undo, tone: "info" }, o?.undo ? 10_000 : (o?.ms ?? 4000)),
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
