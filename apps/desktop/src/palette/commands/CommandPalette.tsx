import { useCallback, useEffect, useRef, useState } from "react";
import { CommandCard, ConfirmBar } from "./CommandCard";
import { QueuePanel, TeamNowPanel } from "./Panels";
import { RoleSwitch } from "./RoleSwitch";
import { SchemaForm } from "./SchemaForm";
import { VerbChips } from "./VerbChips";
import type {
  Answers,
  CommandBridge,
  Command,
  FormSpec,
  Outcome,
  QueueRow,
  RoleState,
  SendOutcome,
  TeamActivity,
} from "./types";

type Panel = "none" | "queue" | "team" | "form";

/** Management commands in the palette (Phase 14): role switch, verb chips, assisted input, forms from the catalogue,
 * the command card with its Effect preview, My queue and Team now. Nothing is sent without Confirm; a reporter with no other
 * role sees nothing here at all. */
export function CommandPalette({ bridge }: { bridge: CommandBridge }) {
  const [state, setState] = useState<RoleState | null>(null);
  const [text, setText] = useState("");
  const [commands, setCommands] = useState<Command[]>([]);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [form, setForm] = useState<{
    spec: FormSpec;
    initial?: Answers;
    errors?: { pointer: string; message: string }[];
  } | null>(null);
  const [panel, setPanel] = useState<Panel>("none");
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [team, setTeam] = useState<TeamActivity[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [opPick, setOpPick] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(
    () => void bridge.state().then(setState),
    [bridge],
  );
  useEffect(refresh, [refresh]);

  const reset = () => {
    setCommands([]);
    setOutcomes([]);
    setNotes([]);
    setForm(null);
  };

  const review = async (cmds: Command[]) => {
    const { outcomes: o } = await bridge.preview(cmds);
    setCommands(cmds);
    setOutcomes(o);
    setForm(null);
  };

  const propose = async () => {
    if (!text.trim()) return;
    setStatus("");
    const p = await bridge.propose(text);
    setNotes(p.notes);
    if (p.error) return setStatus(p.error);
    if (p.commands.length === 0 && p.incomplete.length > 0) {
      const inc = p.incomplete[0];
      setCommands([]);
      setOutcomes([]);
      return setForm({
        spec: inc.form,
        initial: Object.fromEntries(
          Object.entries(inc.args).map(([k, v]) => [
            k,
            Array.isArray(v) ? v.map(String) : String(v),
          ]),
        ),
      });
    }
    if (p.commands.length === 0)
      return setStatus(
        "I could not turn that into a command. Pick an op to open its form.",
      );
    await review(p.commands);
  };

  const openForm = async (op: string) => {
    if (!op) return;
    const spec = await bridge.fields(op);
    if (spec.error) return setStatus(spec.error);
    setOutcomes([]);
    setCommands([]);
    setForm({ spec });
    setPanel("form");
  };

  const submitForm = async (answers: Answers) => {
    if (!form) return;
    const res = await bridge.build(form.spec.op, answers);
    if (res.errors || res.error || !res.command) {
      return setForm({
        ...form,
        initial: answers,
        errors: res.errors ?? [
          { pointer: "", message: res.error ?? "invalid" },
        ],
      });
    }
    await review([res.command]);
  };

  const finish = (r: SendOutcome) => {
    if (r.status === "sent") {
      setStatus(`Sent ${commands.map((c) => c.op).join(", ")}.`);
      reset();
      setText("");
      refresh();
    } else if (r.status === "queued") {
      setStatus("Offline: queued; it will be sent when the server is back.");
      reset();
    } else if (r.status === "rejected") {
      setStatus(
        "Rejected: " +
          (r.rejected ?? [])
            .flatMap((x) => x.errors.map((e) => e.message ?? e.code))
            .join("; "),
      );
    } else {
      setStatus(r.error ?? r.status);
    }
  };

  const confirm = async (second: boolean) => {
    setBusy(true);
    try {
      finish(
        await bridge.send(commands, {
          confirmed: true,
          second_confirmed: second,
        }),
      );
    } finally {
      setBusy(false);
    }
  };

  const show = async (p: Panel) => {
    const next = panel === p ? "none" : p;
    setPanel(next);
    if (next === "queue") setRows((await bridge.queue()).rows ?? []);
    if (next === "team") setTeam((await bridge.teamNow()).activities ?? []);
  };

  if (!state) return null;
  const onlyReporter = state.modes.length <= 1 && state.role === "reporter";
  if (onlyReporter) return null; // a reporter without other roles sees no management verbs

  return (
    <section aria-label="Management commands" className="commands">
      <header>
        <RoleSwitch
          modes={state.modes}
          role={state.role}
          onChange={(m) => {
            reset();
            void bridge.setRole(m).then(setState);
          }}
        />
        {state.review_queue_count ? (
          <small> · {state.review_queue_count} in your queue</small>
        ) : null}
      </header>
      <VerbChips
        chips={state.chips}
        onPick={(c) => {
          setText(c.text);
          input.current?.focus();
        }}
      />
      <label>
        Tell me what to do
        <input
          ref={input}
          value={text}
          placeholder="merge WEB-101 and WEB-105"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void propose();
            } else if (e.key === "Escape") {
              reset();
              setText("");
            }
          }}
        />
      </label>
      <div>
        <label>
          Or fill a form{" "}
          <select value={opPick} onChange={(e) => setOpPick(e.target.value)}>
            <option value="">Choose an op…</option>
            {state.ops.map((o) => (
              <option key={o.op} value={o.op}>
                {o.op}
                {o.destructive ? " (destructive)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => void openForm(opPick)}>Open form</button>
        <button
          aria-pressed={panel === "queue"}
          onClick={() => void show("queue")}
        >
          My queue
        </button>
        <button
          aria-pressed={panel === "team"}
          onClick={() => void show("team")}
        >
          Team now
        </button>
      </div>
      {notes.length ? <p>{notes.join("; ")}</p> : null}
      {status ? <p role="status">{status}</p> : null}
      {form ? (
        <SchemaForm
          spec={form.spec}
          initial={form.initial}
          errors={form.errors}
          onSubmit={(a) => void submitForm(a)}
          onCancel={reset}
        />
      ) : null}
      {outcomes.map((o) => (
        <CommandCard key={o.client_id} outcome={o} />
      ))}
      {outcomes.length ? (
        <ConfirmBar
          outcomes={outcomes}
          busy={busy}
          onConfirm={(s) => void confirm(s)}
          onDiscard={reset}
        />
      ) : null}
      {panel === "queue" ? (
        <QueuePanel
          rows={rows}
          onResolve={(picks) =>
            void bridge.resolve(picks).then((r) => {
              setStatus(
                r.status === "sent"
                  ? `Resolved ${picks.length} row(s).`
                  : (r.error ?? r.status),
              );
              void bridge.queue().then((q) => setRows(q.rows ?? []));
            })
          }
        />
      ) : null}
      {panel === "team" ? <TeamNowPanel activities={team} /> : null}
    </section>
  );
}
