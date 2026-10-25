import type { Row } from "../api/http";

export interface TemplateError {
  pointer: string;
  message: string;
}

/** Parse the JSON editor's text; a syntax error is reported like a schema error (a pointer and a message). */
export function parseBody(text: string): { body: Row | null; errors: TemplateError[] } {
  try {
    const body = JSON.parse(text);
    if (body === null || typeof body !== "object" || Array.isArray(body))
      return { body: null, errors: [{ pointer: "/", message: "A template is a JSON object." }] };
    return { body, errors: [] };
  } catch (e) {
    return {
      body: null,
      errors: [{ pointer: "/", message: `Not valid JSON: ${(e as Error).message}` }],
    };
  }
}

/** Errors that belong to the given slide (or the whole body for `/`), for inline display next to the form row. */
export const errorsAt = (errors: TemplateError[], prefix: string): TemplateError[] =>
  errors.filter((e) => e.pointer === prefix || e.pointer.startsWith(prefix + "/"));

export function blankTemplate(): Row {
  return {
    schema_version: 1,
    key: "my_template",
    name: "My template",
    description: "",
    params: [],
    slides: [{ kind: "title", title_template: "${scope.label}", config: {} }],
    post_rules: [],
  };
}

export function moveSlide(body: Row, from: number, to: number): Row {
  const slides = [...(body.slides ?? [])];
  if (to < 0 || to >= slides.length) return body;
  const [s] = slides.splice(from, 1);
  slides.splice(to, 0, s);
  return { ...body, slides };
}

/** `${name}` placeholders used anywhere in a slide's text fields and config (for the parameter editor's "used by" hint). */
export function placeholdersIn(x: unknown): string[] {
  const out = new Set<string>();
  const walk = (v: unknown) => {
    if (typeof v === "string")
      for (const m of v.matchAll(/\$\{([A-Za-z_][\w.]*)\}/g)) out.add(m[1]);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(x);
  return [...out].sort();
}
