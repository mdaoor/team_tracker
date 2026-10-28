import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, w, type Json } from "../../api/http";
import * as presentApi from "../../api/present";
import type {
  Presentation,
  PresentationTemplate,
  Slide,
  TemplateBody,
  TemplateParam,
} from "../../api/types";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Empty, Field } from "../../components/ui";
import { useList, useWs } from "../../lib/hooks";
import { SLIDE_LABELS } from "../../lib/slides";
import {
  blankTemplate,
  errorsAt,
  moveSlide,
  parseBody,
  placeholdersIn,
  type TemplateError,
} from "../../lib/templates";

export function Templates() {
  const ws = useWs();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toasts = useToasts();
  const list = useList<PresentationTemplate>("/presentation-templates");
  const [editing, setEditing] = useState<{ id: string | null; text: string } | null>(null);
  const [use, setUse] = useState<PresentationTemplate | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: [ws, "/presentation-templates"] });
  const act = useMutation({
    mutationFn: ({
      path,
      method = "post",
      body,
    }: {
      path: string;
      method?: string;
      body?: unknown;
    }) => (method === "delete" ? api.del(w(ws, path)) : api.post<Json>(w(ws, path), body)),
    onSuccess: refresh,
    onError: (e) => toasts.error(e),
  });
  const download = async (t: PresentationTemplate) => {
    const doc = await presentApi.exportTemplate(ws, t.key);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `${t.key}.tt-template.json`;
    a.click();
  };
  const upload = async (f: File | undefined) => {
    if (!f) return;
    const parsed = parseBody(await f.text());
    if (!parsed.body) return toasts.error(new Error(parsed.errors[0].message));
    act.mutate({ path: "/presentation-templates:import", body: { body: parsed.body } });
  };
  if (editing)
    return (
      <Editor
        id={editing.id}
        initial={editing.text}
        onClose={() => {
          setEditing(null);
          refresh();
        }}
      />
    );
  return (
    <div className="present">
      <div className="row">
        <h1>Templates</h1>
        <span className="grow" />
        <Link to="..">Presentations</Link>
        <Button
          onClick={() => setEditing({ id: null, text: JSON.stringify(blankTemplate(), null, 2) })}
        >
          New template
        </Button>
        <label className="btn">
          Import…
          <input
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => void upload(e.target.files?.[0])}
          />
        </label>
      </div>
      {(list.data ?? []).length === 0 ? (
        <Empty>No templates.</Empty>
      ) : (
        <div className="gallery" role="list" aria-label="Templates">
          {(list.data ?? []).map((t) => (
            <div key={t.id} role="listitem" className="tile">
              <strong>{t.name}</strong>
              <small>
                {t.is_builtin ? "built-in" : t.mine ? "mine" : "shared"} · v{t.version} ·{" "}
                {t.slides.length} slides
              </small>
              <small>{t.description}</small>
              <span className="row">
                <Button
                  variant="primary"
                  onClick={() => (t.params.length ? setUse(t) : startFrom(t, {}))}
                >
                  Use
                </Button>
                <Button
                  variant="ghost"
                  onClick={() =>
                    t.is_builtin || !t.mine
                      ? act.mutate({ path: `/presentation-templates/${t.key}:duplicate` })
                      : setEditing({ id: t.key, text: JSON.stringify(t.body, null, 2) })
                  }
                >
                  {t.is_builtin || !t.mine ? "Duplicate" : "Edit"}
                </Button>
                <Button variant="ghost" onClick={() => void download(t)}>
                  Export
                </Button>
                {t.mine && !t.is_builtin && (
                  <>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        act.mutate({
                          path: `/presentation-templates/${t.key}:share`,
                          body: { shared: !t.is_shared },
                        })
                      }
                    >
                      {t.is_shared ? "Unshare" : "Share"}
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        act.mutate({ path: `/presentation-templates/${t.key}`, method: "delete" })
                      }
                    >
                      Delete
                    </Button>
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      {use && (
        <ParamDialog
          tpl={use}
          onCancel={() => setUse(null)}
          onStart={(params) => {
            setUse(null);
            startFrom(use, params);
          }}
        />
      )}
    </div>
  );
  function startFrom(t: PresentationTemplate, params: Record<string, unknown>) {
    presentApi
      .fromTemplate(ws, { template: t.key, params })
      .then((p: Presentation) => nav(`../${p.id}`, { relative: "path" }))
      .catch((e) => toasts.error(e));
  }
}

function ParamDialog({
  tpl,
  onCancel,
  onStart,
}: {
  tpl: PresentationTemplate;
  onCancel: () => void;
  onStart: (p: Record<string, unknown>) => void;
}) {
  const [v, setV] = useState<Record<string, string>>({});
  return (
    <div className="modal" role="dialog" aria-label={`Start from ${tpl.name}`}>
      <h2>{tpl.name}</h2>
      {(tpl.params ?? []).map((p: TemplateParam) => (
        <Field key={p.name} label={p.label ?? p.name}>
          <input
            type={p.type === "int" ? "number" : "text"}
            placeholder={p.default == null ? "" : String(p.default)}
            value={v[p.name] ?? ""}
            onChange={(e) => setV({ ...v, [p.name]: e.target.value })}
          />
        </Field>
      ))}
      <div className="row">
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          variant="primary"
          onClick={() => onStart(Object.fromEntries(Object.entries(v).filter(([, x]) => x !== "")))}
        >
          Create presentation
        </Button>
      </div>
    </div>
  );
}

function Editor({
  id,
  initial,
  onClose,
}: {
  id: string | null;
  initial: string;
  onClose: () => void;
}) {
  const ws = useWs();
  const toasts = useToasts();
  const [text, setText] = useState(initial);
  const [errors, setErrors] = useState<TemplateError[]>([]);
  const [mode, setMode] = useState<"form" | "json">("form");
  const kinds = useList("/presentation/slide-kinds");
  const parsed = useMemo(() => parseBody(text), [text]);
  // validate 400 ms after the last keystroke; syntax errors are shown immediately
  useEffect(() => {
    if (!parsed.body) return setErrors(parsed.errors);
    const t = setTimeout(() => {
      presentApi
        .validateTemplate(ws, parsed.body)
        .then((r) => setErrors(r.errors ?? []))
        .catch((e) => setErrors([{ pointer: "/", message: String(e.message ?? e) }]));
    }, 400);
    return () => clearTimeout(t);
  }, [parsed, ws]);
  const body = parsed.body ?? {};
  const set = (b: TemplateBody) => setText(JSON.stringify(b, null, 2));
  const save = useMutation({
    mutationFn: () => presentApi.saveTemplate(ws, id ?? undefined, body),
    onSuccess: () => {
      toasts.push("Template saved");
      onClose();
    },
    onError: (e) => toasts.error(e),
  });
  const slides: Slide[] = body.slides ?? [];
  const used = placeholdersIn(slides);
  return (
    <div className="present template-editor">
      <div className="row">
        <h1>{id ? `Edit ${body.name ?? id}` : "New template"}</h1>
        <span className="grow" />
        <Button variant={mode === "form" ? "primary" : "default"} onClick={() => setMode("form")}>
          Form
        </Button>
        <Button variant={mode === "json" ? "primary" : "default"} onClick={() => setMode("json")}>
          JSON
        </Button>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          disabled={errors.length > 0 || !parsed.body || save.isPending}
          onClick={() => save.mutate()}
        >
          Save
        </Button>
      </div>
      {errors.length > 0 && (
        <ul className="problems" aria-live="polite" aria-label="Template problems">
          {errors.map((e, i) => (
            <li key={i} role="alert">
              <code>{e.pointer}</code> {e.message}
            </li>
          ))}
        </ul>
      )}
      {mode === "json" ? (
        <textarea
          aria-label="Template JSON"
          className="json"
          rows={28}
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
        />
      ) : (
        <>
          <Card title="Template">
            <Field label="Name">
              <input
                value={body.name ?? ""}
                onChange={(e) => set({ ...body, name: e.target.value })}
              />
            </Field>
            <Field label="Key">
              <input
                value={body.key ?? ""}
                disabled={!!id}
                onChange={(e) => set({ ...body, key: e.target.value })}
              />
            </Field>
            <Field label="Description">
              <input
                value={body.description ?? ""}
                onChange={(e) => set({ ...body, description: e.target.value })}
              />
            </Field>
          </Card>
          <Card
            title="Parameters"
            actions={
              <Button
                onClick={() =>
                  set({
                    ...body,
                    params: [
                      ...(body.params ?? []),
                      {
                        name: `p${(body.params ?? []).length + 1}`,
                        type: "string",
                        required: false,
                      },
                    ],
                  })
                }
              >
                Add parameter
              </Button>
            }
          >
            {(body.params ?? []).map((p: TemplateParam, i: number) => (
              <div key={i} className="row">
                <input
                  aria-label="Parameter name"
                  value={p.name}
                  onChange={(e) =>
                    set({
                      ...body,
                      params: body.params.map((x: TemplateParam, j: number) =>
                        j === i ? { ...x, name: e.target.value } : x,
                      ),
                    })
                  }
                />
                <select
                  aria-label="Parameter type"
                  value={p.type}
                  onChange={(e) =>
                    set({
                      ...body,
                      params: body.params.map((x: TemplateParam, j: number) =>
                        j === i ? { ...x, type: e.target.value } : x,
                      ),
                    })
                  }
                >
                  {[
                    "string",
                    "int",
                    "project",
                    "product",
                    "team",
                    "process",
                    "dimension",
                    "date_range",
                  ].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <label>
                  <input
                    type="checkbox"
                    checked={!!p.required}
                    onChange={(e) =>
                      set({
                        ...body,
                        params: body.params.map((x: TemplateParam, j: number) =>
                          j === i ? { ...x, required: e.target.checked } : x,
                        ),
                      })
                    }
                  />{" "}
                  required
                </label>
                <small>{used.includes(p.name) ? "used" : "unused"}</small>
                <Button
                  variant="ghost"
                  aria-label="Remove parameter"
                  onClick={() =>
                    set({
                      ...body,
                      params: body.params.filter((_: TemplateParam, j: number) => j !== i),
                    })
                  }
                >
                  ✕
                </Button>
              </div>
            ))}
          </Card>
          <Card
            title="Slides"
            actions={
              <Button
                onClick={() =>
                  set({
                    ...body,
                    slides: [
                      ...slides,
                      { kind: "markdown", title_template: "Notes", config: { text: "" } },
                    ],
                  })
                }
              >
                Add slide
              </Button>
            }
          >
            {slides.map((s, i) => (
              <div key={i} className="slide-row">
                <div className="row">
                  <span>{i + 1}.</span>
                  <select
                    aria-label={`Slide ${i + 1} kind`}
                    value={s.kind}
                    onChange={(e) =>
                      set({
                        ...body,
                        slides: slides.map((x, j) =>
                          j === i ? { ...x, kind: e.target.value } : x,
                        ),
                      })
                    }
                  >
                    {(kinds.data ?? []).map((k) => (
                      <option key={k.kind} value={k.kind}>
                        {SLIDE_LABELS[k.kind] ?? k.kind}
                      </option>
                    ))}
                  </select>
                  <input
                    aria-label={`Slide ${i + 1} title`}
                    className="grow"
                    value={s.title_template ?? ""}
                    onChange={(e) =>
                      set({
                        ...body,
                        slides: slides.map((x, j) =>
                          j === i ? { ...x, title_template: e.target.value } : x,
                        ),
                      })
                    }
                  />
                  <Button
                    variant="ghost"
                    aria-label={`Move slide ${i + 1} up`}
                    onClick={() => set(moveSlide(body, i, i - 1))}
                  >
                    ↑
                  </Button>
                  <Button
                    variant="ghost"
                    aria-label={`Move slide ${i + 1} down`}
                    onClick={() => set(moveSlide(body, i, i + 1))}
                  >
                    ↓
                  </Button>
                  <Button
                    variant="ghost"
                    aria-label={`Remove slide ${i + 1}`}
                    onClick={() => set({ ...body, slides: slides.filter((_, j) => j !== i) })}
                  >
                    ✕
                  </Button>
                </div>
                <ConfigBox
                  value={s.config ?? {}}
                  onChange={(c) =>
                    set({
                      ...body,
                      slides: slides.map((x, j) => (j === i ? { ...x, config: c } : x)),
                    })
                  }
                />
                {errorsAt(errors, `/slides/${i}`).map((e, k) => (
                  <small key={k} role="alert" className="err">
                    {e.pointer.replace(`/slides/${i}`, "") || "slide"}: {e.message}
                  </small>
                ))}
              </div>
            ))}
          </Card>
        </>
      )}
    </div>
  );
}

function ConfigBox({ value, onChange }: { value: Json; onChange: (v: Json) => void }) {
  const [text, setText] = useState(JSON.stringify(value));
  const [bad, setBad] = useState(false);
  useEffect(() => {
    if (!bad) setText(JSON.stringify(value));
  }, [value, bad]);
  return (
    <input
      aria-label="Slide config (JSON)"
      className={bad ? "bad" : ""}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        try {
          onChange(JSON.parse(e.target.value));
          setBad(false);
        } catch {
          setBad(true);
        }
      }}
    />
  );
}
