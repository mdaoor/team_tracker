import { autocompletion, type CompletionContext } from "@codemirror/autocomplete";
import { defaultKeymap } from "@codemirror/commands";
import { StreamLanguage, syntaxHighlighting, HighlightStyle } from "@codemirror/language";
import { EditorState, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, keymap } from "@codemirror/view";
import { useEffect, useRef } from "react";
import type { ContextFunction, ContextSchema } from "../../api/types";
import { problemRange, type CelProblem } from "../../lib/governance";

const KEYWORDS = new Set(["true", "false", "null", "in"]);
const cel = StreamLanguage.define<unknown>({
  token(stream) {
    if (stream.eatSpace()) return null;
    if (stream.match(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/)) return "string";
    if (stream.match(/\d+(\.\d+)?/)) return "number";
    if (stream.match(/[A-Za-z_]\w*/))
      return KEYWORDS.has(stream.current()) ? "keyword" : "variableName";
    stream.next();
    return "operator";
  },
});
import { tags as t } from "@lezer/highlight";
const hl = HighlightStyle.define([
  { tag: t.string, color: "var(--good)" },
  { tag: t.number, color: "var(--s3)" },
  { tag: t.keyword, color: "var(--s4)" },
  { tag: t.variableName, color: "var(--text-1)" },
  { tag: t.operator, color: "var(--text-3)" },
]);

/** Completion from the context schema: `item.` → fields, bare word → roots + functions. */
export function completionsFor(
  schema: ContextSchema | undefined,
  before: string,
): { label: string; type: string; detail?: string }[] {
  if (!schema) return [];
  const dot = /([A-Za-z_]\w*)\.(\w*)$/.exec(before);
  if (dot) {
    const obj =
      schema.objects?.[schema.roots?.[dot[1]] ?? dot[1]] ?? schema.objects?.[dot[1]] ?? {};
    const fields = Object.entries(obj).map(([k, v]) => ({
      label: k,
      type: "property",
      detail: String(v),
    }));
    const fns = (schema.functions ?? [])
      .filter((f: ContextFunction) =>
        f.namespace ? f.namespace === dot[1] : f.receiver && schema.roots?.[dot[1]] === f.receiver,
      )
      .map((f: ContextFunction) => ({ label: f.name, type: "function", detail: f.doc }));
    return [...fields, ...fns];
  }
  return [
    ...Object.entries(schema.roots ?? {}).map(([k, v]) => ({
      label: k,
      type: "variable",
      detail: String(v),
    })),
    ...(schema.functions ?? [])
      .filter((f: ContextFunction) => !f.receiver && !f.namespace)
      .map((f: ContextFunction) => ({ label: f.name, type: "function", detail: f.doc })),
  ];
}

const setRanges = StateEffect.define<[number, number][]>();
const marks = StateField.define({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects)
      if (e.is(setRanges))
        deco = Decoration.set(
          e.value.map(([a, b]) =>
            Decoration.mark({ class: "cel-err" }).range(a, Math.max(b, a + 1)),
          ),
          true,
        );
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

export function CelEditor({
  value,
  onChange,
  schema,
  problems,
  readOnly = false,
  label = "CEL condition",
}: {
  value: string;
  onChange?: (v: string) => void;
  schema?: ContextSchema;
  problems: CelProblem[];
  readOnly?: boolean;
  label?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const cb = useRef(onChange);
  const sc = useRef(schema);
  cb.current = onChange;
  sc.current = schema;
  useEffect(() => {
    const complete = (c: CompletionContext) => {
      const w = c.matchBefore(/[\w.]*/);
      if (!w || (w.from === w.to && !c.explicit)) return null;
      const before = c.state.sliceDoc(0, c.pos);
      const word = /(\w*)$/.exec(before)?.[1] ?? "";
      return { from: c.pos - word.length, options: completionsFor(sc.current, before) };
    };
    view.current = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          keymap.of(defaultKeymap),
          cel,
          marks,
          syntaxHighlighting(hl),
          autocompletion({ override: [complete] }),
          EditorView.lineWrapping,
          EditorState.readOnly.of(readOnly),
          EditorView.contentAttributes.of({ "aria-label": label, "data-testid": "cel-editor" }),
          EditorView.updateListener.of((u) => u.docChanged && cb.current?.(u.state.doc.toString())),
        ],
      }),
    });
    return () => view.current?.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly]);
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value)
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const src = v.state.doc.toString();
    const ranges = problems
      .filter((p) => p.line || p.column)
      .map((p) => problemRange(src, p))
      .map(([a, b]): [number, number] => [Math.min(a, src.length), Math.min(b, src.length)])
      .filter(([a, b]) => b > a || a < src.length);
    v.dispatch({ effects: setRanges.of(ranges) });
  }, [problems]);
  return <div ref={host} className="cel-editor" />;
}
