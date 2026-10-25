import { useId, useState } from "react";
import type { Answers, FieldSpec, FormSpec } from "./types";

/** A form generated from the op's JSON Schema. The sidecar produces the fields spec (types, enums, required, pack-backed pickers);
 * this component only renders it, so the CLI, Slack and the desktop offer the same inputs and produce the same `ClientCommand`. */
export function SchemaForm({
  spec,
  initial,
  errors,
  onSubmit,
  onCancel,
}: {
  spec: FormSpec;
  initial?: Answers;
  errors?: { pointer: string; message: string }[];
  onSubmit: (a: Answers) => void;
  onCancel: () => void;
}) {
  const [a, setA] = useState<Answers>(initial ?? {});
  const uid = useId();
  const set = (k: string, v: string | string[] | boolean) =>
    setA((x) => ({ ...x, [k]: v }));
  const err = (name: string) =>
    errors?.find((e) => e.pointer === `/${name}` || e.pointer === name)
      ?.message;
  return (
    <form
      aria-label={`Form ${spec.op}`}
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(a);
      }}
    >
      <p>{spec.description}</p>
      {spec.fields.map((f) => (
        <Field
          key={f.name}
          f={f}
          id={`${uid}-${f.name}`}
          value={a[f.name]}
          error={err(f.name)}
          onChange={(v) => set(f.name, v)}
        />
      ))}
      {errors?.some(
        (e) => !spec.fields.some((f) => `/${f.name}` === e.pointer),
      ) ? (
        <p role="alert">
          {errors
            .filter((e) => !spec.fields.some((f) => `/${f.name}` === e.pointer))
            .map((e) => e.message)
            .join("; ")}
        </p>
      ) : null}
      <button type="submit">Preview</button>
      <button type="button" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

function Field({
  f,
  id,
  value,
  error,
  onChange,
}: {
  f: FieldSpec;
  id: string;
  value: string | string[] | boolean | undefined;
  error?: string;
  onChange: (v: string | string[] | boolean) => void;
}) {
  const label = `${f.label}${f.required ? " (required)" : ""}`;
  const common = {
    id,
    required: f.required,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": error ? `${id}-err` : undefined,
  } as const;
  const listId = `${id}-list`;
  let control: JSX.Element;
  if (f.kind === "boolean") {
    control = (
      <input
        {...common}
        required={false}
        type="checkbox"
        checked={value === true}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  } else if (f.kind === "enum" && f.multi) {
    control = (
      <select
        {...common}
        multiple
        value={Array.isArray(value) ? value : []}
        onChange={(e) =>
          onChange(Array.from(e.target.selectedOptions).map((o) => o.value))
        }
      >
        {f.choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );
  } else if (f.kind === "enum") {
    control = (
      <select
        {...common}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{f.required ? "Choose…" : "—"}</option>
        {f.choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );
  } else if (f.kind === "json") {
    control = (
      <textarea
        {...common}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  } else {
    const type =
      f.kind === "integer" || f.kind === "number"
        ? "number"
        : f.kind === "date"
          ? "date"
          : "text";
    const text = Array.isArray(value) ? value.join(", ") : (value ?? "");
    control = (
      <>
        <input
          {...common}
          type={type}
          list={f.choices.length ? listId : undefined}
          value={typeof text === "boolean" ? "" : text}
          placeholder={f.multi ? "comma separated" : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
        {f.choices.length ? (
          <datalist id={listId}>
            {f.choices.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </datalist>
        ) : null}
      </>
    );
  }
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label> {control}
      {f.description ? <small> {f.description}</small> : null}
      {error ? (
        <small id={`${id}-err`} role="alert">
          {" "}
          {error}
        </small>
      ) : null}
    </div>
  );
}
