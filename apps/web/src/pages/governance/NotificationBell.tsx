import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { markNotificationRead } from "../../api/governance";
import { useList, useWs } from "../../lib/hooks";

/** In-app notifications (Phase 8 `/notifications/mine`): a bell with the unread count and a list that deep-links. */
export function NotificationBell() {
  const ws = useWs();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const all = useList("/notifications/mine");
  const unread = (all.data ?? []).filter((n) => !n.read_at);
  const read = useMutation({
    mutationFn: (id: string) => markNotificationRead(ws, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [ws, "/notifications/mine"] }),
  });
  return (
    <span className="bell">
      <button
        className="btn btn-ghost"
        aria-label={`Notifications, ${unread.length} unread`}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        🔔{unread.length > 0 && <b className="badge">{unread.length}</b>}
      </button>
      {open && (
        <div className="bell-menu" role="menu" aria-label="Notifications">
          {(all.data ?? []).length === 0 && <p>No notifications.</p>}
          {(all.data ?? []).slice(0, 20).map((n) => (
            <button
              key={n.id}
              role="menuitem"
              className={n.read_at ? "read" : "unread"}
              onClick={() => {
                read.mutate(n.id);
                setOpen(false);

                nav(`/w/${ws}/violations`);
              }}
            >
              <strong>{n.title ?? n.kind}</strong>
              <small>{n.body}</small>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
