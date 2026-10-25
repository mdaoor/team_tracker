import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { violationAction } from "../../api/governance";
import type { Violation } from "../../api/types";
import { AddToPresentation } from "../../components/AddToPresentation";
import { useToasts } from "../../components/Toasts";
import { Button, Chip, Empty, Pill } from "../../components/ui";
import type { Severity } from "../../lib/confidence";
import { ageLabel, evidenceChips, groupViolations, SEVERITIES } from "../../lib/governance";
import { useList, useMe, useWs } from "../../lib/hooks";

type Act = "acknowledge" | "resolve" | "waive" | "false-positive";

export function Violations() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can, me } = useMe();
  const [sp, setSp] = useSearchParams();
  const f = {
    status: sp.get("status") ?? "open,acknowledged",
    severity: sp.get("severity") ?? undefined,
    policy: sp.get("policy") ?? undefined,
    category: sp.get("category") ?? undefined,
  };
  const mine = sp.get("who") === "mine";
  const list = useList<Violation>("/violations", { ...f, limit: 500 });
  const rows = useMemo(
    () => (list.data ?? []).filter((v) => !mine || (me && v.person_id === me.person_id)),
    [list.data, mine, me],
  );
  const groups = useMemo(() => groupViolations(rows), [rows]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const [modal, setModal] = useState<{ act: Act; ids: string[] } | null>(null);
  const [text, setText] = useState("");

  const set = (k: string, v: string | null) =>
    setSp((p) => {
      if (v) p.set(k, v);
      else p.delete(k);
      return p;
    });
  const act = useMutation({
    mutationFn: async ({ act, ids, text }: { act: Act; ids: string[]; text: string }) => {
      for (const id of ids) {
        const body =
          act === "waive" || act === "false-positive"
            ? { reason: text }
            : act === "resolve"
              ? { note: text }
              : undefined;
        await violationAction(ws, id, act, body);
      }
    },
    onSuccess: () => {
      setSel(new Set());
      setModal(null);
      setText("");
      void qc.invalidateQueries({ queryKey: [ws, "/violations"] });
      toasts.push("Updated");
    },
    onError: (e) => toasts.error(e),
  });
  const run = (a: Act, ids: string[]) => {
    if (!ids.length) return;
    if (a === "waive" || a === "false-positive") setModal({ act: a, ids });
    else act.mutate({ act: a, ids, text: "" });
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (modal || /INPUT|TEXTAREA|SELECT/.test(el.tagName) || e.metaKey || e.ctrlKey) return;
      const ids = sel.size ? [...sel] : focus ? [focus] : [];
      const m: Record<string, Act> = {
        a: "acknowledge",
        r: "resolve",
        w: "waive",
        f: "false-positive",
      };
      if (m[e.key]) run(m[e.key], ids);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  const toggle = (id: string) =>
    setSel((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="gov">
      <div className="row">
        <h1>Violations</h1>
        <AddToPresentation
          build={() => ({
            kind: "violations",
            title: "Violations",
            config: { with_habits: false },
          })}
        />
        <span className="grow" />
        <select
          aria-label="Status"
          value={f.status}
          onChange={(e) => set("status", e.target.value)}
        >
          <option value="open,acknowledged">Open</option>
          <option value="resolved">Resolved</option>
          <option value="waived">Waived</option>
          <option value="false_positive">False positive</option>
        </select>
        <select
          aria-label="Severity"
          value={f.severity ?? ""}
          onChange={(e) => set("severity", e.target.value || null)}
        >
          <option value="">Any severity</option>
          {SEVERITIES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select
          aria-label="Whose"
          value={mine ? "mine" : ""}
          onChange={(e) => set("who", e.target.value || null)}
        >
          <option value="">Mine and my team</option>
          <option value="mine">Mine only</option>
        </select>
        {f.policy && <Chip onRemove={() => set("policy", null)}>policy: {f.policy}</Chip>}
      </div>
      {sel.size > 0 && (
        <div className="row bulk" role="toolbar" aria-label="Bulk actions">
          <span>{sel.size} selected</span>
          {can["violation.resolve"] && (
            <Button onClick={() => run("acknowledge", [...sel])}>Acknowledge</Button>
          )}
          {can["violation.resolve"] && (
            <Button onClick={() => run("resolve", [...sel])}>Resolve</Button>
          )}
          {can["violation.waive"] && <Button onClick={() => run("waive", [...sel])}>Waive…</Button>}
        </div>
      )}
      {rows.length === 0 ? (
        <Empty>Nothing here. {f.status.startsWith("open") ? "No open violations." : ""}</Empty>
      ) : (
        groups.map((g) => (
          <section key={g.severity} aria-label={`${g.severity} violations`}>
            <h2>
              <Pill severity={g.severity as Severity}>{g.severity}</Pill>{" "}
              {g.policies.reduce((n, p) => n + p.rows.length, 0)}
            </h2>
            {g.policies.map((p) => (
              <div key={p.policy}>
                <h3>{String(p.policy).replaceAll("_", " ")}</h3>
                <ul className="plain violations">
                  {p.rows.map((v) => (
                    <li
                      key={v.id}
                      className={`row vrow ${focus === v.id ? "focus" : ""}`}
                      onClick={() => setFocus(v.id)}
                      data-testid="violation"
                    >
                      <input
                        type="checkbox"
                        aria-label={`Select ${v.target}`}
                        checked={sel.has(v.id)}
                        onChange={() => toggle(v.id)}
                      />
                      <a
                        href={`?item=${v.evidence?.item ?? v.target}`}
                        onClick={(e) => {
                          e.preventDefault();
                          setSp((q) => (q.set("item", v.evidence?.item ?? v.target_id), q));
                        }}
                      >
                        {v.evidence?.item ?? v.target}
                      </a>
                      <span className="chips">
                        {evidenceChips(v.evidence).map((c) => (
                          <Chip key={c}>{c}</Chip>
                        ))}
                      </span>
                      <small>{v.person ?? "—"}</small>
                      <small>{ageLabel(v.detected_at)}</small>
                      <span className="grow" />
                      {v.status !== "open" && (
                        <small title={v.waive_reason ?? v.resolution_note ?? ""}>{v.status}</small>
                      )}
                      {(v.status === "open" || v.status === "acknowledged") && (
                        <span className="row">
                          {v.status === "open" && (
                            <Button variant="ghost" onClick={() => run("acknowledge", [v.id])}>
                              Ack
                            </Button>
                          )}
                          <Button variant="ghost" onClick={() => run("resolve", [v.id])}>
                            Resolve
                          </Button>
                          {can["violation.waive"] && (
                            <Button variant="ghost" onClick={() => run("waive", [v.id])}>
                              Waive
                            </Button>
                          )}
                          <Button variant="ghost" onClick={() => run("false-positive", [v.id])}>
                            False positive
                          </Button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        ))
      )}
      <small>Keys: a acknowledge · r resolve · w waive · f false positive</small>
      {modal && (
        <div
          className="modal"
          role="dialog"
          aria-label={modal.act === "waive" ? "Waive violation" : "Mark as false positive"}
        >
          <h2>
            {modal.act === "waive" ? "Waive" : "False positive"} ({modal.ids.length})
          </h2>
          <label>
            Reason (required)
            <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <div className="row">
            <Button onClick={() => setModal(null)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={!text.trim() || act.isPending}
              onClick={() => act.mutate({ ...modal, text })}
            >
              Confirm
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
