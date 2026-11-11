import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { api, w, type Row } from "../../api/http";
import { Button, Field } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useWs } from "../../lib/hooks";

/** A tiny form: fields by name, POST to `path`, refresh the listed query keys. */
export function CreateForm({
  path,
  fields,
  refresh,
  transform,
  label = "Add",
  children,
}: {
  path: string;
  fields: {
    name: string;
    label: string;
    type?: string;
    required?: boolean;
    options?: string[];
    placeholder?: string;
  }[];
  refresh: string[];
  transform?: (v: Row) => Row;
  label?: string;
  children?: ReactNode;
}) {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const [v, setV] = useState<Row>({});
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const body: Row = {};
      for (const f of fields)
        if (v[f.name] !== undefined && v[f.name] !== "")
          body[f.name] = f.type === "number" ? Number(v[f.name]) : v[f.name];
      await api.post(w(ws, path), transform ? transform(body) : body);
      setV({});
      await Promise.all(refresh.map((k) => qc.invalidateQueries({ queryKey: [ws, k] })));
    } catch (err) {
      toasts.error(err);
    }
  };
  return (
    <form className="row form" onSubmit={submit} aria-label={label}>
      {fields.map((f) => (
        <Field key={f.name} label={f.label}>
          {f.options ? (
            <select
              value={v[f.name] ?? ""}
              required={f.required}
              onChange={(e) => setV({ ...v, [f.name]: e.target.value })}
            >
              <option value="">—</option>
              {f.options.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ) : (
            <input
              type={f.type ?? "text"}
              value={v[f.name] ?? ""}
              required={f.required}
              placeholder={f.placeholder}
              onChange={(e) => setV({ ...v, [f.name]: e.target.value })}
            />
          )}
        </Field>
      ))}
      {children}
      <Button variant="primary" type="submit">
        {label}
      </Button>
    </form>
  );
}

export function Table({
  rows,
  cols,
}: {
  rows: Row[];
  cols: { key: string; label: string; render?: (r: Row) => ReactNode }[];
}) {
  return (
    <table>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c.key}>{c.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.id ?? r.key ?? i}>
            {cols.map((c) => (
              <td key={c.key}>{c.render ? c.render(r) : String(r[c.key] ?? "")}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
