import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Button } from "./ui";

type Ask = { title: string; body?: string; confirmLabel?: string; danger?: boolean };
type Ctx = (a: Ask) => Promise<boolean>;
const C = createContext<Ctx>(() => Promise.resolve(true));
export const useConfirm = () => useContext(C);

/** A modal confirmation (native `<dialog>`: focus trap, Esc to cancel). Resolves true on confirm. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [ask, setAsk] = useState<Ask | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);
  const confirm = useCallback<Ctx>((a) => {
    setAsk(a);
    return new Promise<boolean>((res) => {
      resolver.current = res;
    });
  }, []);
  const done = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setAsk(null);
  };
  return (
    <C.Provider value={confirm}>
      {children}
      {ask && (
        <dialog
          open
          className="confirm"
          aria-labelledby="confirm-h"
          ref={(el) => el?.querySelector<HTMLElement>("[data-autofocus]")?.focus()}
          onKeyDown={(e) => e.key === "Escape" && done(false)}
        >
          <h2 id="confirm-h">{ask.title}</h2>
          {ask.body && <p>{ask.body}</p>}
          <div className="row">
            <Button data-autofocus onClick={() => done(false)}>
              Cancel
            </Button>
            <Button variant={ask.danger === false ? "primary" : "default"} onClick={() => done(true)}>
              {ask.confirmLabel ?? "Delete"}
            </Button>
          </div>
        </dialog>
      )}
    </C.Provider>
  );
}
