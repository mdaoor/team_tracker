import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, w, type Row } from "../../api/http";
import { useConfirm } from "../../components/Confirm";
import { useToasts } from "../../components/Toasts";
import { downloadPost } from "../../lib/download";
import { Button, Empty, Field } from "../../components/ui";
import { useList, useMe, useObj, useWs } from "../../lib/hooks";
import { SLIDE_ICONS, SLIDE_LABELS } from "../../lib/slides";
import { Templates } from "./Templates";

const DIMS = ["assignee", "team", "project", "product", "stage", "priority", "epic", "tags"];
const MEASURES = ["count", "hours", "wt", "viol"];
type Spec = {
  k: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "json" | "check";
  options?: string[];
};

const FORMS: Record<string, Spec[]> = {
  explorer: [
    { k: "dimA", label: "Rows", type: "select", options: DIMS },
    { k: "dimB", label: "Columns", type: "select", options: ["", ...DIMS] },
    { k: "measure", label: "Measure", type: "select", options: MEASURES },
  ],
  pipeline: [{ k: "process", label: "Process (key)", type: "text" }],
  radar: [{ k: "days", label: "Horizon (days)", type: "number" }],
  gantt: [
    { k: "rows", label: "Rows", type: "select", options: ["epic", "item", "milestone"] },
    { k: "baseline", label: "Baseline name", type: "text" },
  ],
  project_detail: [{ k: "project_key", label: "Project key", type: "text" }],
  insight: [{ k: "scope", label: "Project key (empty = all)", type: "text" }],
  view: [{ k: "saved_view_id", label: "Saved view", type: "select" }],
  chart: [
    { k: "rows", label: "Rows", type: "select", options: DIMS },
    { k: "cols", label: "Columns", type: "select", options: ["", ...DIMS] },
    { k: "measure", label: "Measure", type: "select", options: ["count", "hours", "weighted_throughput", "sum_weight", "open_violations"] },
    { k: "filter", label: "Filter (JSON)", type: "json" },
  ],
  table: [
    { k: "filter", label: "Filter (JSON)", type: "json" },
    { k: "limit", label: "Rows", type: "number" },
  ],
  markdown: [{ k: "text", label: "Text (markdown)", type: "textarea" }],
  image: [{ k: "url", label: "Image URL (https or data:)", type: "text" }],
  violations: [{ k: "with_habits", label: "Show habits next to violations", type: "check" }],
  roadmap: [
    { k: "project_key", label: "Project key (empty = every planned project in scope)", type: "text" },
    { k: "show_baseline", label: "Show baseline end markers", type: "check" },
  ],
  milestones: [{ k: "project_key", label: "Project key (empty = all)", type: "text" }],
  deliverables: [
    { k: "project_key", label: "Project key", type: "text" },
    { k: "phase", label: "Phase key (empty = all phases)", type: "text" },
  ],
  plan_vs_actual: [{ k: "project_key", label: "Project key", type: "text" }],
  phase_status: [{ k: "project_key", label: "Project key", type: "text" }],
  project_overview: [{ k: "project_key", label: "Project key", type: "text" }],
}; // prettier-ignore

export function PresentRoute() {
  const id = useParams()["*"]?.split("/")[0];
  if (id === "templates") return <Templates />;
  return id ? <Builder id={id} /> : <PresentList />;
}

function PresentList() {
  const ws = useWs();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toasts = useToasts();
  const decks = useList("/presentations");
  const tpls = useList("/presentations/templates");
  const fromTemplate = useMutation({
    mutationFn: (template: string) =>
      api.post<Row>(w(ws, "/presentations:from-template"), { template }),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: [ws, "/presentations"] });
      nav(p.id);
    },
    onError: (e) => toasts.error(e),
  });
  const [brief, setBrief] = useState("");
  const [importText, setImportText] = useState("");
  const done = (p: Row) => {
    void qc.invalidateQueries({ queryKey: [ws, "/presentations"] });
    nav(p.id);
  };
  const author = useMutation({
    mutationFn: () => api.post<Row>(w(ws, "/presentations:author"), { brief }),
    onSuccess: done,
    onError: (e) => toasts.error(e),
  });
  const importDeck = useMutation({
    mutationFn: () => {
      const deck: unknown = JSON.parse(importText);
      return api.post<Row>(w(ws, "/presentations:import"), { deck, lenient: true });
    },
    onSuccess: done,
    onError: (e) => toasts.error(e),
  });
  const downloadPack = () =>
    downloadPost(
      toasts,
      w(ws, "/presentations:generation-pack"),
      { brief },
      "tt-generation-pack.zip",
      "generation pack",
    );
  return (
    <div className="present">
      <div className="row">
        <h1>Presentations</h1>
        <span className="grow" />
        <Link to="templates">Templates</Link>
      </div>
      <section aria-label="Generate with AI">
        <h2>Generate with AI</h2>
        <textarea
          aria-label="Brief"
          rows={3}
          value={brief}
          placeholder="e.g. A quarterly review of the ERP rollout for the steering committee"
          onChange={(e) => setBrief(e.target.value)}
        />
        <div className="row">
          <Button
            disabled={brief.trim().length < 3 || author.isPending}
            onClick={() => author.mutate()}
          >
            Generate with AI
          </Button>
          <Button onClick={() => void downloadPack()}>Download pack for an external LLM</Button>
        </div>
        <details>
          <summary>Import a deck an external LLM wrote</summary>
          <textarea
            aria-label="Deck JSON"
            rows={6}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
          />
          <Button
            disabled={!importText.trim() || importDeck.isPending}
            onClick={() => importDeck.mutate()}
          >
            Import deck
          </Button>
        </details>
      </section>
      <section aria-label="Start from template">
        <h2>Start from template</h2>
        <div className="row">
          {(tpls.data ?? []).map((t) => (
            <Button key={t.key} onClick={() => fromTemplate.mutate(t.key)}>
              {t.title}
            </Button>
          ))}
        </div>
      </section>
      <section aria-label="Your presentations">
        <h2>Presentations</h2>
        {(decks.data ?? []).length === 0 ? (
          <Empty>
            No presentations yet. Start from a template above, or use “Add to presentation” on any
            view.
          </Empty>
        ) : (
          <ul className="plain">
            {(decks.data ?? []).map((p) => (
              <li key={p.id} className="row">
                <Link to={p.id}>{p.title}</Link>
                <small>
                  {p.slides.length} slides · {p.scope} · {p.range_days} d
                  {p.is_shared ? " · shared" : ""}
                  {p.mine ? "" : " · by a colleague"}
                </small>
                <a href={`/p/${ws}/${p.id}`} target="_blank" rel="noreferrer">
                  Present ↗
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Builder({ id }: { id: string }) {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const nav = useNavigate();
  const { me, admin } = useMe();
  const shareOn = !!me?.settings?.external_share;
  const deck = useObj(`/presentations/${id}`);
  const projects = useList("/projects");
  const products = useList("/products");
  const teams = useList("/teams");
  const people = useList("/people");
  const schemes = useList("/weight-schemes");
  const views = useList("/views");
  const [sel, setSel] = useState<string | null>(null);
  const [kind, setKind] = useState("kpis");
  const [rev, setRev] = useState(0);
  const [previewLoaded, setPreviewLoaded] = useState("");
  const p = deck.data as Row | undefined;
  const slides = useMemo<Row[]>(() => p?.slides ?? [], [p]);
  const cur = slides.find((s) => s.id === sel) ?? slides[0];

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: [ws, `/presentations/${id}`] });
    setRev((r) => r + 1);
  };
  const save = useMutation({
    mutationFn: ({
      path,
      body,
      method,
    }: {
      path: string;
      body?: unknown;
      method: "patch" | "post" | "put" | "del";
    }) => (method === "del" ? api.del(w(ws, path)) : api[method](w(ws, path), body as never)),
    onSuccess: refresh,
    onError: (e) => toasts.error(e),
  });
  const patch = (body: Row) => save.mutate({ path: `/presentations/${id}`, body, method: "patch" });
  const patchSlide = (sid: string, body: Row) =>
    save.mutate({ path: `/presentations/${id}/slides/${sid}`, body, method: "patch" });
  const move = (i: number, d: number) => {
    const ids = slides.map((s) => s.id);
    const j = i + d;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    save.mutate({ path: `/presentations/${id}/slides:reorder`, body: { ids }, method: "put" });
  };
  const confirm = useConfirm();
  const remove = useMutation({
    mutationFn: () => api.del(w(ws, `/presentations/${id}`)),
    onSuccess: () => {
      const title = p?.title ?? "Presentation";
      nav(`/w/${ws}/present`);
      void qc.invalidateQueries({ queryKey: [ws, "/presentations"] });
      toasts.push(`Deleted “${title}”`, {
        undo: async () => {
          try {
            await api.post(w(ws, `/presentations/${id}:restore`), {});
            void qc.invalidateQueries({ queryKey: [ws, "/presentations"] });
            nav(`/w/${ws}/present/${id}`);
          } catch (e) {
            toasts.error(e);
          }
        },
      });
    },
    onError: (e) => toasts.error(e),
  });
  const askDelete = async () => {
    if (
      await confirm({
        title: `Delete “${p?.title ?? "this presentation"}”?`,
        body: "Its share link stops working. You can undo for a few seconds.",
      })
    )
      remove.mutate();
  };
  const share = useMutation({
    mutationFn: (enabled: boolean) =>
      api.post<Row>(w(ws, `/presentations/${id}/share`), { enabled }),
    onSuccess: (r) => (
      refresh(),
      toasts.push(r.url ? `Link: ${location.origin}${r.url}` : "Link disabled", { ms: 12000 })
    ),
    onError: (e) => toasts.error(e),
  });
  const download = (format: string) =>
    downloadPost(
      toasts,
      w(ws, `/presentations/${id}/render?format=${format}`),
      {},
      `${p?.title ?? "presentation"}.${["png", "csv"].includes(format) ? "zip" : format}`,
      format.toUpperCase(),
    );

  useEffect(() => {
    if (!sel && slides[0]) setSel(slides[0].id);
  }, [sel, slides]);

  if (deck.isLoading || !p) return <p>Loading…</p>;
  const scopeOptions = [
    ["workspace", "Whole workspace"],
    ...(products.data ?? []).map((x) => [`product:${x.key}`, `Product ${x.name}`]),
    ...(projects.data ?? []).map((x) => [`project:${x.key}`, `Project ${x.name}`]),
    ...(teams.data ?? []).map((x) => [`team:${x.slug}`, `Team ${x.name}`]),
    ...(people.data ?? []).map((x) => [
      `person:${x.handle}`,
      `Person ${x.display_name ?? x.handle}`,
    ]),
  ];
  const idx = slides.findIndex((s) => s.id === cur?.id);
  return (
    <div
      className="builder"
      style={{ display: "grid", gridTemplateColumns: "260px 300px 1fr", gap: 16 }}
    >
      <aside aria-label="Presentation settings">
        <Field label="Title">
          <input
            defaultValue={p.title}
            onBlur={(e) => e.target.value !== p.title && patch({ title: e.target.value })}
          />
        </Field>
        <Field label="Scope">
          <select value={p.scope} onChange={(e) => patch({ scope: e.target.value })}>
            {scopeOptions.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Range">
          <select value={p.range_days} onChange={(e) => patch({ range_days: +e.target.value })}>
            {[7, 14, 30, 90].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </select>
        </Field>
        <Field label="Weights">
          <select
            value={p.weight_scheme_id ?? ""}
            onChange={(e) => patch({ weight_scheme_id: e.target.value || null })}
          >
            <option value="">default</option>
            {(schemes.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Theme">
          <select value={p.theme} onChange={(e) => patch({ theme: e.target.value })}>
            {["auto", "light", "dark"].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </Field>
        <label className="row">
          <input
            type="checkbox"
            checked={p.is_shared}
            onChange={(e) => patch({ is_shared: e.target.checked })}
          />{" "}
          Visible to everyone in the workspace
        </label>
        <div className="row">
          <Button
            onClick={() => share.mutate(!p.shared_externally)}
            disabled={!shareOn && !p.shared_externally}
            title={shareOn ? undefined : "External sharing is switched off for this workspace"}
          >
            {p.shared_externally ? "Stop link sharing" : "Create read-only link"}
          </Button>
          {!shareOn && !p.shared_externally && (
            <small className="hint">
              External sharing is off.{" "}
              {admin ? (
                <button
                  className="btn btn-ghost"
                  onClick={() => void api.patch(w(ws, "/settings"), { external_share: true }).then(() => Promise.all([qc.invalidateQueries({ queryKey: [ws, "/me"] }), qc.invalidateQueries({ queryKey: [ws, "me"] })]))}
                >
                  Turn it on
                </button>
              ) : (
                "Ask a workspace admin to turn it on."
              )}
            </small>
          )}
        </div>
        <h3>Present &amp; export</h3>
        <div className="row">
          <a className="btn btn-primary" href={`/p/${ws}/${id}`} target="_blank" rel="noreferrer">
            ▶ Present
          </a>
          {["html", "pdf", "pptx", "png", "csv"].map((f) => (
            <Button key={f} onClick={() => void download(f)}>
              {f.toUpperCase()}
            </Button>
          ))}
        </div>
        <div className="row">
          <Button variant="ghost" onClick={() => void askDelete()}>
            Delete presentation
          </Button>
        </div>
      </aside>

      <section aria-label="Slides">
        <h2>Slides</h2>
        <ol className="slide-list" aria-label="Slide order">
          {slides.map((s, i) => (
            <li key={s.id} className={s.id === cur?.id ? "sel" : ""}>
              <button
                className="slide-pick"
                aria-current={s.id === cur?.id}
                onClick={() => setSel(s.id)}
              >
                <span aria-hidden>{SLIDE_ICONS[s.kind] ?? "▫"}</span> {i + 1}.{" "}
                {s.title || SLIDE_LABELS[s.kind] || s.kind}
              </button>
              <span className="row">
                <button
                  aria-label={`Move ${s.title ?? s.kind} up`}
                  onClick={() => move(i, -1)}
                  disabled={i === 0}
                >
                  ↑
                </button>
                <button
                  aria-label={`Move ${s.title ?? s.kind} down`}
                  onClick={() => move(i, 1)}
                  disabled={i === slides.length - 1}
                >
                  ↓
                </button>
                <button
                  aria-label={`Duplicate ${s.title ?? s.kind}`}
                  onClick={() =>
                    save.mutate({
                      path: `/presentations/${id}/slides`,
                      body: { slide: { ...s, id: undefined }, position: i + 1 },
                      method: "post",
                    })
                  }
                >
                  ⧉
                </button>
                <button
                  aria-label={`Remove ${s.title ?? s.kind}`}
                  onClick={() =>
                    save.mutate({ path: `/presentations/${id}/slides/${s.id}`, method: "del" })
                  }
                >
                  ✕
                </button>
              </span>
            </li>
          ))}
        </ol>
        <div className="row">
          <select aria-label="Slide kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(SLIDE_LABELS).map(([k, l]) => (
              <option key={k} value={k}>
                {SLIDE_ICONS[k]} {l}
              </option>
            ))}
          </select>
          <Button
            onClick={() => {
              const cfg: Row =
                kind === "markdown"
                  ? { text: "New slide" }
                  : kind === "project_detail"
                    ? { project_key: projects.data?.[0]?.key }
                    : kind === "chart"
                      ? { rows: "project" }
                      : kind === "view"
                        ? { saved_view_id: views.data?.[0]?.id }
                        : {};
              save.mutate({
                path: `/presentations/${id}/slides`,
                body: { slide: { kind, config: cfg } },
                method: "post",
              });
            }}
          >
            Add slide
          </Button>
        </div>
      </section>

      <section aria-label="Slide editor">
        {cur ? (
          <>
            <h2>
              {idx + 1}. {cur.title || SLIDE_LABELS[cur.kind]}
            </h2>
            <div className={previewLoaded === `${rev}-${cur.id}` ? "" : "skeleton"} style={{ aspectRatio: "16/9" }}>
              <iframe
                key={`${rev}-${cur.id}`}
                title="Slide preview"
                src={`/p/${ws}/${id}?slide=${idx + 1}`}
                onLoad={() => setPreviewLoaded(`${rev}-${cur.id}`)}
                style={{
                  width: "100%",
                  height: "100%",
                  border: "1px solid var(--line)",
                  borderRadius: 8,
                }}
              />
            </div>
            <Field label="Slide title">
              <input
                key={cur.id + "t"}
                defaultValue={cur.title ?? ""}
                onBlur={(e) =>
                  e.target.value !== (cur.title ?? "") &&
                  patchSlide(cur.id, { title: e.target.value || null })
                }
              />
            </Field>
            {(FORMS[cur.kind] ?? []).map((f) => (
              <Field key={cur.id + f.k} label={f.label}>
                <ConfigInput
                  spec={f}
                  value={cur.config[f.k]}
                  options={
                    f.k === "saved_view_id"
                      ? (views.data ?? []).map((v) => v.id + "|" + v.name)
                      : f.options
                  }
                  onChange={(v) => patchSlide(cur.id, { config: { ...cur.config, [f.k]: v } })}
                />
              </Field>
            ))}
            <Field label="Speaker notes">
              <textarea
                key={cur.id + "n"}
                rows={4}
                defaultValue={cur.notes ?? ""}
                onBlur={(e) =>
                  e.target.value !== (cur.notes ?? "") &&
                  patchSlide(cur.id, { notes: e.target.value || null })
                }
              />
            </Field>
          </>
        ) : (
          <Empty>No slides yet. Add one from the middle column.</Empty>
        )}
      </section>
    </div>
  );
}

function ConfigInput({
  spec,
  value,
  options,
  onChange,
}: {
  spec: Spec;
  value: unknown;
  options?: string[];
  onChange: (v: unknown) => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  if (spec.type === "select")
    return (
      <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)}>
        {!options?.includes("") && <option value="">—</option>}
        {(options ?? []).map((o) => {
          const [v, l] = o.split("|");
          return (
            <option key={v} value={v}>
              {l ?? (v || "—")}
            </option>
          );
        })}
      </select>
    );
  if (spec.type === "check")
    return <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  if (spec.type === "number")
    return (
      <input
        type="number"
        defaultValue={Number(value ?? 0)}
        onBlur={(e) => onChange(+e.target.value)}
      />
    );
  if (spec.type === "textarea")
    return (
      <textarea
        rows={6}
        defaultValue={String(value ?? "")}
        onBlur={(e) => onChange(e.target.value)}
      />
    );
  if (spec.type === "json")
    return (
      <>
        <textarea
          rows={4}
          defaultValue={JSON.stringify(value ?? {}, null, 1)}
          onBlur={(e) => {
            try {
              onChange(JSON.parse(e.target.value || "{}"));
              setErr(null);
            } catch {
              setErr("Not valid JSON");
            }
          }}
        />
        {err && <small role="alert">{err}</small>}
      </>
    );
  return (
    <input
      defaultValue={String(value ?? "")}
      onBlur={(e) => e.target.value !== String(value ?? "") && onChange(e.target.value || null)}
    />
  );
}
