import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../api/http";
import { useList, useWs } from "../lib/hooks";
import type { SlideDraft } from "../lib/slides";
import { Button } from "./ui";
import { useToasts } from "./Toasts";

/** The "export any report" path: appends the current view/chart as a slide of an existing or a new presentation. */
export function AddToPresentation({ build }: { build: () => SlideDraft }) {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const [open, setOpen] = useState(false);
  const decks = useList("/presentations", undefined, open);
  const add = useMutation({
    mutationFn: async (target: string | null) => {
      const slide = build();
      let id = target;
      if (!id) {
        const p = await api.post<Row>(w(ws, "/presentations"), {
          title: "New presentation",
          slides: [],
        });
        id = p.id as string;
      }
      await api.post(w(ws, `/presentations/${id}/slides`), { slide });
      return id;
    },
    onSuccess: (id) => {
      setOpen(false);
      void qc.invalidateQueries({ queryKey: [ws, "/presentations"] });
      toasts.push("Added to presentation", { ms: 4000 });
      return id;
    },
    onError: (e) => toasts.error(e),
  });
  return (
    <span className="add-to-pres">
      <Button aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        Add to presentation
      </Button>
      {open && (
        <div role="menu" className="menu">
          {(decks.data ?? [])
            .filter((p) => p.mine)
            .map((p) => (
              <button
                key={p.id}
                role="menuitem"
                className="menu-item"
                onClick={() => add.mutate(p.id)}
              >
                {p.title}
              </button>
            ))}
          <button role="menuitem" className="menu-item" onClick={() => add.mutate(null)}>
            ＋ New presentation…
          </button>
        </div>
      )}
    </span>
  );
}
