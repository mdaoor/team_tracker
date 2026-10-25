import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { generateInsights, setInsightStatus } from "../../api/governance";
import type { Insight, Project } from "../../api/types";
import { AddToPresentation } from "../../components/AddToPresentation";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty } from "../../components/ui";
import { useList, useWs } from "../../lib/hooks";

const NEXT: [string, string][] = [
  ["reviewed", "Reviewed"],
  ["actioned", "Actioned"],
  ["dismissed", "Dismiss"],
];

export function Insights() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const [, setSp] = useSearchParams();
  const [status, setStatus] = useState("");
  const [project, setProject] = useState("");
  const projects = useList<Project>("/projects");
  const list = useList<Insight>("/insights", {
    ...(status ? { status } : {}),
    ...(project ? { project } : {}),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: [ws, "/insights"] });
  const set = useMutation({
    mutationFn: ({ id, s }: { id: string; s: string }) =>
      setInsightStatus(ws, id, s),
    onSuccess: refresh,
    onError: (e) => toasts.error(e),
  });
  const regen = useMutation({
    mutationFn: () => generateInsights(ws, project),
    onSuccess: () => {
      refresh();
      toasts.push("Insight regenerated");
    },
    onError: (e) => toasts.error(e),
  });
  return (
    <div className="gov">
      <div className="row">
        <h1>Insights</h1>
        <AddToPresentation
          build={() => ({ kind: "insight", title: "Insights", config: { scope: null } })}
        />
        <span className="grow" />
        <select aria-label="Project" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">All projects</option>
          {(projects.data ?? []).map((p: Project) => (
            <option key={p.id} value={p.key}>
              {p.key}
            </option>
          ))}
        </select>
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {["new", "reviewed", "actioned", "dismissed"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <Button disabled={!project || regen.isPending} onClick={() => regen.mutate()}>
          Regenerate
        </Button>
      </div>
      {(list.data ?? []).length === 0 ? (
        <Empty>No insights yet.</Empty>
      ) : (
        (list.data ?? []).map((i) => (
          <Card key={i.id} title={`${i.project} · ${i.kind} · ${i.status}`}>
            <p>{i.body}</p>
            {(i.supporting_ids ?? []).length > 0 && (
              <p>
                <small>Evidence: </small>
                {(i.supporting_ids as string[]).slice(0, 8).map((id) => (
                  <a
                    key={id}
                    href={`?item=${id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      setSp((p) => (p.set("item", id), p));
                    }}
                  >
                    {id.slice(0, 8)}{" "}
                  </a>
                ))}
              </p>
            )}
            <div className="row">
              {NEXT.filter(([s]) => s !== i.status).map(([s, l]) => (
                <Button key={s} variant="ghost" onClick={() => set.mutate({ id: i.id, s })}>
                  {l}
                </Button>
              ))}
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
