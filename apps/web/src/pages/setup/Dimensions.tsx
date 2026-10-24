import { useState } from "react";
import { api, w } from "../../api/http";
import { Card } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { CreateForm } from "./common";
import { useQueryClient } from "@tanstack/react-query";

export function Dimensions() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const dims = useList("/dimensions");
  const [csv, setCsv] = useState<Record<string, string>>({});
  const paste = async (key: string) => {
    const values = (csv[key] ?? "")
      .split(/[\n,]/)
      .map((x) => x.trim())
      .filter(Boolean);
    try {
      await api.post(w(ws, `/dimensions/${key}/values:bulk`), values);
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
              <button className="btn" onClick={() => paste(d.key)}>
                Add values
              </button>
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
