import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, w, type Row } from "../../api/http";
import { AddToPresentation } from "../../components/AddToPresentation";
import { useToasts } from "../../components/Toasts";
import { Button, Card, Field } from "../../components/ui";
import { formulaFrom, rowsFrom, type MultRow } from "../../lib/governance";
import { useList, useMe, useWs } from "../../lib/hooks";

export function Weights() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const { can } = useMe();
  const schemes = useList("/weight-schemes");
  const [sel, setSel] = useState("");
  const [name, setName] = useState("");
  const [base, setBase] = useState("estimate_hours");
  const [constant, setConstant] = useState("1");
  const [rows, setRows] = useState<MultRow[]>([]);
  const [lo, setLo] = useState("");
  const [hi, setHi] = useState("");
  const [prev, setPrev] = useState<Row | null>(null);
  const cur = (schemes.data ?? []).find((s) => s.id === sel);
  useEffect(() => {
    if (!cur) return;
    setName(cur.name);
    setBase(cur.formula?.base ?? "constant");
    setConstant(String(cur.formula?.constant ?? 1));
    setRows(rowsFrom(cur.formula));
    setLo(String(cur.formula?.clamp?.[0] ?? ""));
    setHi(String(cur.formula?.clamp?.[1] ?? ""));
  }, [cur]);
  const formula = formulaFrom(base, constant, rows, lo, hi);
  const preview = useMutation({
    mutationFn: () => api.post<Row>(w(ws, "/weight-schemes:preview"), { formula, sample: 20 }),
    onSuccess: setPrev,
    onError: (e) => toasts.error(e),
  });
  const create = useMutation({
    mutationFn: async () => {
      const s = await api.post<Row>(w(ws, "/weight-schemes"), { name, formula });
      await api.post(w(ws, `/weight-schemes/${s.id}/recompute`));
      return s;
    },
    onSuccess: (s) => {
      void qc.invalidateQueries({ queryKey: [ws, "/weight-schemes"] });
      setSel(s.id);
      toasts.push("Scheme saved and recomputed");
    },
    onError: (e) => toasts.error(e),
  });
  const [okey, setOkey] = useState("");
  const [oval, setOval] = useState("");
  const override = useMutation({
    mutationFn: () =>
      api.put(w(ws, `/items/${okey}/weights/${cur?.name ?? ""}`), { value: Number(oval) }),
    onSuccess: () => toasts.push("Override saved"),
    onError: (e) => toasts.error(e),
  });
  const editable = !!can["weight.set"];
  return (
    <div className="gov">
      <h1>Weights</h1>
      <AddToPresentation
        build={() => ({
          kind: "table",
          title: "Weighted items",
          config: { filter: {}, sort: [{ field: "priority", dir: "desc" }], limit: 50 },
          live: true,
        })}
      />
      <p>
        <small>
          Views and exports use the scheme you pick in their own selector; the default scheme
          applies otherwise.
        </small>
      </p>
      <div className="row">
        <select aria-label="Scheme" value={sel} onChange={(e) => setSel(e.target.value)}>
          <option value="">New scheme</option>
          {(schemes.data ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
      <Card
        title={
          sel
            ? "Formula (a saved scheme is immutable: change it by saving a new one)"
            : "New scheme"
        }
      >
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Base">
          <select value={base} onChange={(e) => setBase(e.target.value)}>
            <option value="estimate_hours">Estimate (hours)</option>
            <option value="points">Points</option>
            <option value="constant">Constant</option>
          </select>
        </Field>
        {base === "constant" && (
          <Field label="Constant">
            <input type="number" value={constant} onChange={(e) => setConstant(e.target.value)} />
          </Field>
        )}
        <table aria-label="Multipliers">
          <thead>
            <tr>
              <th>Dimension.value</th>
              <th>Factor</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>
                  <input
                    aria-label="Multiplier key"
                    list="mult-keys"
                    placeholder="priority.high"
                    value={r.key}
                    onChange={(e) =>
                      setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label="Factor"
                    type="number"
                    step="0.1"
                    value={r.factor}
                    onChange={(e) =>
                      setRows(rows.map((x, j) => (j === i ? { ...x, factor: e.target.value } : x)))
                    }
                  />
                </td>
                <td>
                  <Button
                    variant="ghost"
                    aria-label="Remove row"
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}
                  >
                    ✕
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <datalist id="mult-keys">
          {[
            "priority.low",
            "priority.medium",
            "priority.high",
            "priority.urgent",
            "type.bug",
            "type.task",
            "type.story",
          ].map((k) => (
            <option key={k} value={k} />
          ))}
        </datalist>
        <div className="row">
          <Button onClick={() => setRows([...rows, { key: "", factor: "1" }])}>
            Add multiplier
          </Button>
          <Field label="Clamp min">
            <input type="number" value={lo} onChange={(e) => setLo(e.target.value)} />
          </Field>
          <Field label="max">
            <input type="number" value={hi} onChange={(e) => setHi(e.target.value)} />
          </Field>
        </div>
        <div className="row">
          <Button onClick={() => preview.mutate()} disabled={!editable}>
            Preview on 20 items
          </Button>
          {!sel && (
            <Button
              variant="primary"
              disabled={!editable || !name || create.isPending}
              onClick={() => create.mutate()}
            >
              Save and recompute
            </Button>
          )}
        </div>
      </Card>
      {prev && (
        <Card title={`Preview · total ${prev.total_current} → ${prev.total_preview}`}>
          <table aria-label="Weight preview">
            <thead>
              <tr>
                <th>Item</th>
                <th>Now</th>
                <th>With formula</th>
              </tr>
            </thead>
            <tbody>
              {prev.data.map((r: Row) => (
                <tr key={r.key}>
                  <td>
                    {r.key} <small>{r.title}</small>
                  </td>
                  <td>{r.current}</td>
                  <td>{r.preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {sel && editable && (
        <Card title={`Override one item under “${cur?.name}”`}>
          <div className="row">
            <input
              aria-label="Item key"
              placeholder="WEB-12"
              value={okey}
              onChange={(e) => setOkey(e.target.value)}
            />
            <input
              aria-label="Weight"
              type="number"
              value={oval}
              onChange={(e) => setOval(e.target.value)}
            />
            <Button
              disabled={!okey || oval === "" || override.isPending}
              onClick={() => override.mutate()}
            >
              Set
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
