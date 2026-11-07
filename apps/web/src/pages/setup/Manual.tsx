import { useState } from "react";
import { getContextPack } from "../../api/teams";
import { Button, Card } from "../../components/ui";
import { useToasts } from "../../components/Toasts";
import { useList, useObj, useWs } from "../../lib/hooks";

const FIELDS = ["action", "item", "project", "minutes", "priority", "milestone", "stage"];

export function Manual() {
  const ws = useWs();
  const toasts = useToasts();
  const pack = useObj("/context-pack");
  const people = useList("/people");
  const [who, setWho] = useState("");
  const required: string[] = pack.data?.manual_mode?.required_fields ?? [];
  const exportPack = async () => {
    try {
      const data = await getContextPack(ws, who);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = `context-pack-${who || "me"}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toasts.error(e);
    }
  };
  return (
    <Card title="Manual mode & context pack">
      <p className="muted">
        Fields the manual form insists on (the server never rejects a capture for missing fields).
      </p>
      <ul className="checks">
        {FIELDS.map((f) => (
          <li key={f}>
            <label>
              <input type="checkbox" checked={required.includes(f)} readOnly /> {f}
            </label>
          </li>
        ))}
      </ul>
      <label>
        Export for{" "}
        <select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Export pack for">
          <option value="">me</option>
          {(people.data ?? []).map((p) => (
            <option key={p.handle}>{p.handle}</option>
          ))}
        </select>
      </label>
      <Button onClick={exportPack}>Export context pack</Button>
    </Card>
  );
}
