import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, w, type Row } from "../api/http";
import { Button, ConfidenceCue, Empty, Pill } from "../components/ui";
import { useToasts } from "../components/Toasts";
import { useList, useWs } from "../lib/hooks";
import { t } from "../lib/i18n";

export const DEFAULT_THRESHOLD = 0.7;

/** The suggestion's primary choice for a row, if any (stored suggestion or the LLM gateway's best guess). */
export function suggestedChoice(row: Row): Row | null {
  const llm = row.suggestion?.llm?.suggestions?.[0]?.choice;
  if (llm) return llm;
  const s = row.suggestion ?? {};
  switch (row.kind) {
    case "stage_suggestion":
      return s.stage ? { stage: s.stage } : null;
    case "product_suggestion":
      return s.product ? { product: s.product } : null;
    case "unconfirmed_classification":
      return { action: "confirm" };
    case "unclassified_project":
      return s.project ? { project: s.project } : null;
    default:
      return null;
  }
}

export function ReviewQueue() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const rows = useList("/review-queue");
  const projects = useList("/projects");
  const people = useList("/people");
  const [cursor, setCursor] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [threshold, setThreshold] = useState(DEFAULT_THRESHOLD);
  const data = useMemo(() => rows.data ?? [], [rows.data]);

  // Live updates (ADR-0009): SSE, with a 30 s poll as the fallback.
  useEffect(() => {
    let es: EventSource | null = null;
    const refresh = () => qc.invalidateQueries({ queryKey: [ws, "/review-queue"] });
    try {
      es = new EventSource(w(ws, "/review-queue/stream"));
      es.addEventListener("queue", refresh);
    } catch {
      /* polling below */
    }
    const poll = setInterval(refresh, 30_000);
    return () => {
      es?.close();
      clearInterval(poll);
    };
  }, [ws, qc]);

  const resolve = useCallback(
    async (row: Row, choice: Row) => {
      try {
        await api.post(w(ws, "/review-queue/resolve"), { ref: row.ref, choice });
        if (row.kind === "unclassified_project" && row.suggestion?.phrase && choice.project)
          toasts.push(`Next time "${row.suggestion.phrase}" → ${choice.project}`);
        await qc.invalidateQueries({ queryKey: [ws, "/review-queue"] });
      } catch (e) {
        toasts.error(e);
      }
    },
    [ws, qc, toasts],
  );

  // keyboard: j/k move, Enter takes the suggestion, x selects
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest?.("input,select,textarea")) return;
      const row = data[cursor];
      if (e.key === "j") setCursor((c) => Math.min(c + 1, data.length - 1));
      else if (e.key === "k") setCursor((c) => Math.max(c - 1, 0));
      else if (e.key === "x" && row)
        setSelected((s) =>
          s.has(row.ref) ? new Set([...s].filter((r) => r !== row.ref)) : new Set(s).add(row.ref),
        );
      else if (e.key === "Enter" && row) {
        const choice = suggestedChoice(row);
        if (choice) void resolve(row, choice);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [data, cursor, resolve]);

  const confirmAll = async () => {
    try {
      const done = await api.post<Row>(w(ws, "/review-queue/confirm-all-suggested"), {});
      toasts.push(`Confirmed ${done.confirmed} suggested`);
      await qc.invalidateQueries({ queryKey: [ws, "/review-queue"] });
    } catch (e) {
      toasts.error(e);
    }
  };
  const bulkProject = async (project: string) => {
    try {
      for (const row of data.filter(
        (r) => selected.has(r.ref) && r.kind === "unclassified_project",
      ))
        await api.post(w(ws, "/review-queue/resolve"), { ref: row.ref, choice: { project } });
      setSelected(new Set());
      await qc.invalidateQueries({ queryKey: [ws, "/review-queue"] });
    } catch (e) {
      toasts.error(e);
    }
  };
  const eligible = data.filter(
    (r) => (r.confidence ?? 0) >= threshold && suggestedChoice(r),
  ).length;

  return (
    <section aria-labelledby="rq-h">
      <h1 id="rq-h">{t("nav.review")}</h1>
      <div className="bulk row">
        <label>
          Bulk: assign project{" "}
          <select
            aria-label="Assign project to selected"
            disabled={!selected.size}
            onChange={(e) => e.target.value && bulkProject(e.target.value)}
            value=""
          >
            <option value="">…</option>
            {(projects.data ?? [])
              .filter((p) => p.kind !== "inbox")
              .map((p) => (
                <option key={p.key} value={p.key}>
                  {p.key}
                </option>
              ))}
          </select>
        </label>
        <label>
          min confidence {Math.round(threshold * 100)} %{" "}
          <input
            type="range"
            min={0}
            max={100}
            value={threshold * 100}
            onChange={(e) => setThreshold(Number(e.target.value) / 100)}
            aria-label="Confidence threshold"
          />
        </label>
        <Button variant="primary" onClick={confirmAll} disabled={!eligible}>
          {t("review.confirmAll")} ({eligible})
        </Button>
      </div>
      {rows.isSuccess && data.length === 0 && <Empty>{t("review.empty")}</Empty>}
      <ul className="rq" role="listbox" aria-label="Review rows">
        {data.map((r, i) => {
          const choice = suggestedChoice(r);
          return (
            <li
              key={r.ref}
              role="option"
              aria-selected={i === cursor}
              className={i === cursor ? "rq-row cur" : "rq-row"}
              data-kind={r.kind}
              onClick={() => setCursor(i)}
            >
              <input
                type="checkbox"
                aria-label={`select ${r.item_key}`}
                checked={selected.has(r.ref)}
                onChange={() =>
                  setSelected((s) =>
                    s.has(r.ref)
                      ? new Set([...s].filter((x) => x !== r.ref))
                      : new Set(s).add(r.ref),
                  )
                }
              />
              <a className="key" href={`?item=${r.item_key}`}>
                {r.item_key}
              </a>
              <span className="grow">{r.item_title}</span>
              <Pill severity="info">{String(r.kind).replaceAll("_", " ")}</Pill>
              <RowActions
                row={r}
                choice={choice}
                projects={projects.data ?? []}
                people={people.data ?? []}
                onResolve={resolve}
              />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function RowActions({
  row,
  choice,
  projects,
  people,
  onResolve,
}: {
  row: Row;
  choice: Row | null;
  projects: Row[];
  people: Row[];
  onResolve: (r: Row, c: Row) => void;
}) {
  const label = choice ? Object.values(choice)[0] : null;
  return (
    <span className="row">
      {choice && (
        <Button variant="primary" onClick={() => onResolve(row, choice)}>
          {String(label)}{" "}
          <ConfidenceCue
            confidence={row.confidence}
            confirmed={row.kind === "unconfirmed_classification" && false}
          />
        </Button>
      )}
      {row.kind === "unclassified_project" && (
        <select
          aria-label="other project"
          value=""
          onChange={(e) => e.target.value && onResolve(row, { project: e.target.value })}
        >
          <option value="">other…</option>
          {projects
            .filter((p) => p.kind !== "inbox")
            .map((p) => (
              <option key={p.key} value={p.key}>
                {p.key}
              </option>
            ))}
        </select>
      )}
      {row.kind === "unknown_person" && (
        <select
          aria-label="pick person"
          value=""
          onChange={(e) => e.target.value && onResolve(row, { handle: e.target.value })}
        >
          <option value="">pick person…</option>
          {people.map((p) => (
            <option key={p.handle} value={p.handle}>
              {p.handle}
            </option>
          ))}
        </select>
      )}
      {row.kind === "unconfirmed_classification" ? (
        <Button onClick={() => onResolve(row, { action: "reject" })}>Reject</Button>
      ) : (
        <Button variant="ghost" onClick={() => onResolve(row, { ignore: true })}>
          Ignore
        </Button>
      )}
    </span>
  );
}
