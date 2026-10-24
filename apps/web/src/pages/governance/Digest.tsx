import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, w, type Row } from "../../api/http";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty } from "../../components/ui";
import { useList, useMe, useObj, useWs } from "../../lib/hooks";

export function Digest() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can } = useMe();
  const teams = useList("/teams");
  const [team, setTeam] = useState("");
  const [days, setDays] = useState(14);
  const d = useObj("/digest", { days, ...(team ? { team } : {}) }, !!can["dailylog.review"]);
  const data = d.data as Row | undefined;
  const review = useMutation({
    mutationFn: (id: string) => api.post(w(ws, `/daily-logs/${id}/review`)),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [ws, "/digest"] }),
    onError: (e) => toasts.error(e),
  });
  if (!can["dailylog.review"]) return <Empty>The digest is for managers.</Empty>;
  return (
    <div className="gov">
      <div className="row">
        <h1>Manager digest</h1>
        <span className="grow" />
        <select aria-label="Team" value={team} onChange={(e) => setTeam(e.target.value)}>
          <option value="">Whole workspace</option>
          {(teams.data ?? []).map((t: Row) => (
            <option key={t.id} value={t.slug}>
              {t.name}
            </option>
          ))}
        </select>
        <select aria-label="Window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 30].map((x) => (
            <option key={x} value={x}>
              {x} days
            </option>
          ))}
        </select>
      </div>
      {data && (
        <>
          <Card title={`Daily logs · ${data.unreviewed} to review`}>
            {data.daily_logs.length === 0 ? (
              <Empty>No daily logs in this window.</Empty>
            ) : (
              <ul className="plain">
                {data.daily_logs.map((l: Row) => (
                  <li key={l.id} className="row">
                    <strong>{l.person}</strong>{" "}
                    <small>
                      {l.date} · {l.status}
                    </small>
                    <span className="grow">{l.summary}</span>
                    {l.status === "confirmed" && (
                      <Button onClick={() => review.mutate(l.id)}>Mark reviewed</Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Notable events">
            {data.events.length === 0 ? (
              <Empty>Nothing notable.</Empty>
            ) : (
              <ul className="plain">
                {data.events.map((e: Row) => (
                  <li key={e.id}>
                    <small>{String(e.occurred_at).slice(0, 10)}</small> {e.event_type} ·{" "}
                    {e.entity_id}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Pinned notes">
            {data.notes.length === 0 ? (
              <Empty>No pinned notes.</Empty>
            ) : (
              <ul className="plain">
                {data.notes.map((n: Row) => (
                  <li key={n.id}>
                    <strong>{n.title}</strong> {n.body}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Insights">
            {data.insights.length === 0 ? (
              <Empty>No insights in this window.</Empty>
            ) : (
              <ul className="plain">
                {data.insights.map((i: Row) => (
                  <li key={i.id}>
                    <strong>{i.project}</strong> {i.body}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
