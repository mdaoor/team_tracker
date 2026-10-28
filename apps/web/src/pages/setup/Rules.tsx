import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { deleteRule, dryRunRule } from "../../api/teams";
import type { DryRunResult } from "../../api/types";
import { Button, Card, Empty } from "../../components/ui";
import { useConfirm } from "../../components/Confirm";
import { useToasts } from "../../components/Toasts";
import { useList, useWs } from "../../lib/hooks";
import { CreateForm } from "./common";

export function Rules() {
  const ws = useWs();
  const qc = useQueryClient();
  const toasts = useToasts();
  const confirm = useConfirm();
  const rules = useList("/translator-rules");
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<DryRunResult | null>(null);
  const active = (rules.data ?? [])
    .filter((r) => r.is_active !== false)
    .map((r) => r.text as string);
  const dry = async () => {
    try {
      setResult(await dryRunRule(ws, { rules: draft ? [...active, draft] : active }));
    } catch (e) {
      toasts.error(e);
    }
  };
  return (
    <Card title="Rules for the translator">
      <ol>
        {(rules.data ?? []).map((r) => (
          <li key={r.id}>
            {r.text}{" "}
            <Button
              variant="ghost"
              aria-label={`delete rule ${r.position}`}
              onClick={async () => {
                if (
                  !(await confirm({
                    title: `Delete rule ${r.position}?`,
                    body: `“${r.text}” stops steering the translator.`,
                  }))
                )
                  return;
                try {
                  await deleteRule(ws, r.id);
                  await qc.invalidateQueries({ queryKey: [ws, "/translator-rules"] });
                } catch (e) {
                  toasts.error(e);
                }
              }}
            >
              ✕
            </Button>
          </li>
        ))}
      </ol>
      <CreateForm
        path="/translator-rules"
        refresh={["/translator-rules"]}
        label="Add rule"
        fields={[
          {
            name: "text",
            label: "Rule",
            required: true,
            placeholder: "prod incident -> priority critical",
          },
        ]}
      />
      <label>
        Try a rule before saving{" "}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Candidate rule"
        />
      </label>
      <Button onClick={dry}>Dry run on the last 200 lines</Button>
      {result &&
        (result.available ? (
          <div aria-label="Dry run result">
            <p>
              Would change {result.changed} of {result.checked} lines.
            </p>
            <ul>
              {result.results.map((r) => (
                <li key={r.line}>
                  {r.line} → {(r.fields as string[]).join(", ")}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <Empty>Dry run unavailable: {result.reason}</Empty>
        ))}
    </Card>
  );
}
