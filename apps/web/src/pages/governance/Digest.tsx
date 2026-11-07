import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { reviewDailyLog } from "../../api/governance";
import type { Digest as DigestData, Json, Team } from "../../api/types";
import { AddToPresentation } from "../../components/AddToPresentation";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty } from "../../components/ui";
import { digestMarkdown } from "../../lib/governance";
import { useList, useMe, useObj, useWs } from "../../lib/hooks";

export function Digest() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can } = useMe();
  const teams = useList<Team>("/teams");
  const [team, setTeam] = useState("");
  const [days, setDays] = useState(14);
  const d = useObj<DigestData>(
    "/digest",
    { days, ...(team ? { team } : {}) },
    !!can["dailylog.review"],
  );
  const data = d.data;
  const review = useMutation({
    mutationFn: (id: string) => reviewDailyLog(ws, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [ws, "/digest"] }),
    onError: (e) => toasts.error(e),
  });
  if (!can["dailylog.review"]) return <Empty>The digest is for managers.</Empty>;
  return (
    <div className="gov">
      <div className="row">
        <h1>Manager digest</h1>
        <span className="grow" />
        {data && (
          <AddToPresentation
            build={() => ({
              kind: "markdown",
              title: "Manager digest",
              config: { text: digestMarkdown(data) },
            })}
          />
        )}
        <select aria-label="Team" value={team} onChange={(e) => setTeam(e.target.value)}>
          <option value="">Whole workspace</option>
          {(teams.data ?? []).map((t: Team) => (
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
                {data.daily_logs.map((l: Json) => (
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
                {data.events.map((e: Json) => (
                  <li key={e.id}>
                    <small>{String(e.occurred_at).slice(0, 10)}</small> {e.event_type} · {e.entity}
                    {e.actor ? ` · by ${e.actor}` : ""}
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
                {data.notes.map((n: Json) => (
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
                {data.insights.map((i: Json) => (
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
