import { useState } from "react";
import { bulkDimensionValues } from "../../api/teams";
import { Card } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { CreateForm } from "./common";
import { useQueryClient } from "@tanstack/react-query";

/** Pasted text (one per line, or comma separated; quotes stripped) to a de-duplicated list, order kept. */
export function parseValues(text: string): string[] {
  const seen = new Set<string>();
  for (const raw of text.split(/[\n,;\t]/)) {
    const v = raw
      .trim()
      .replace(/^"(.*)"$/, "$1")
      .trim();
    if (v) seen.add(v);
  }
  return [...seen];
}

export function Dimensions() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const dims = useList("/dimensions");
  const [csv, setCsv] = useState<Record<string, string>>({});
  const paste = async (key: string) => {
    const values = parseValues(csv[key] ?? "");
    try {
      await bulkDimensionValues(ws, key, values);
      setCsv({ ...csv, [key]: "" });
      await qc.invalidateQueries({ queryKey: [ws, `/dimensions/${key}/values`] });
      toasts.push(`Added ${values.length} value(s) to ${key}`);
    } catch (e) {
      toasts.error(e);
    }
  };
  return (
    <>
      <Card title="Add dimension">
        <CreateForm
          path="/dimensions"
          refresh={["/dimensions"]}
          label="Add dimension"
          fields={[
            { name: "key", label: "Key", required: true },
            { name: "name", label: "Name", required: true },
            { name: "value_type", label: "Shape", options: ["flat", "tree"] },
            { name: "cardinality", label: "Cardinality", options: ["single", "multi"] },
          ]}
        />
      </Card>
      {(dims.data ?? []).map((d) => (
        <Card key={d.key} title={`${d.name} (${d.key}) · ${d.kind}`}>
          <Values dim={d.key} />
          {d.kind === "custom" && (
            <div className="row">
              <textarea
                aria-label={`Paste values for ${d.key}`}
                rows={2}
                placeholder="paste one value per line or comma separated"
                value={csv[d.key] ?? ""}
                onChange={(e) => setCsv({ ...csv, [d.key]: e.target.value })}
              />
              <button
                className="btn"
                disabled={!parseValues(csv[d.key] ?? "").length}
                onClick={() => paste(d.key)}
              >
                Add values
              </button>
              <small className="muted" aria-live="polite">
                {parseValues(csv[d.key] ?? "").length
                  ? `${parseValues(csv[d.key] ?? "").length} values to add`
                  : ""}
              </small>
            </div>
          )}
        </Card>
      ))}
    </>
  );
}

function Values({ dim }: { dim: string }) {
  const v = useList(`/dimensions/${dim}/values`);
  return <p>{(v.data ?? []).map((x) => x.name).join(" · ") || "no values"}</p>;
}
