import { useEffect, useId, useRef, useState } from "react";
import { Dialog } from "./Dialog";
import { clock, hm } from "./format";
import type { Draft, PersonalBridge, PrivacyInfo, SentRow } from "./types";

type Mode = "ask" | "auto" | "never";
const MODES: [Mode, string][] = [
  ["ask", "Ask me each time"],
  ["auto", "Send automatically"],
  ["never", "Never"],
];

/** First-run explainer and privacy settings: the three category toggles (work, meetings, development). Personal has no toggle: it
 * cannot be sent, and the text says so. */
export function PrivacyDialog({
  info,
  firstRun,
  bridge,
  onClose,
  onSaved,
  announce,
}: {
  info: PrivacyInfo;
  firstRun: boolean;
  bridge: PersonalBridge;
  onClose: () => void;
  onSaved: (p: PrivacyInfo) => void;
  announce: (msg: string, error?: boolean) => void;
}) {
  const [work, setWork] = useState<Mode>(info.categories.work);
  const [meeting, setMeeting] = useState<Mode>(info.categories.meeting);
  const [development, setDevelopment] = useState(
    info.categories.development === "on",
  );
  const [sent, setSent] = useState<SentRow[] | null>(null);
  const [confirmErase, setConfirmErase] = useState(false);
  const gid = useId();

  useEffect(() => {
    if (!firstRun) void bridge.sent().then((r) => setSent(r.sent));
  }, [bridge, firstRun]);

  const save = async () => {
    const p = await bridge.firstRun({ work, meeting, development });
    onSaved(p);
    announce("Privacy choices saved");
    onClose();
  };
  const unsend = async (s: SentRow) => {
    try {
      const r = await bridge.unsend(s.item);
      announce(
        r.how === "discarded"
          ? "Taken back before it reached the server"
          : "Correction queued",
      );
      setSent((await bridge.sent()).sent);
    } catch (e) {
      announce((e as Error).message, true);
    }
  };
  const erase = async () => {
    const r = await bridge.eraseLocal();
    announce(`Erased ${r.files.join(", ")} and ${r.secrets.length} secret(s)`);
    onClose();
  };

  const radio = (name: string, value: Mode, set: (m: Mode) => void) => (
    <div role="radiogroup" aria-labelledby={`${gid}-${name}`}>
      {MODES.map(([m, label]) => (
        <label key={m} className="choice">
          <input
            type="radio"
            name={`${gid}-${name}`}
            checked={value === m}
            onChange={() => set(m)}
          />
          {label}
        </label>
      ))}
    </div>
  );

  return (
    <Dialog
      title={firstRun ? "Your time stays yours" : "Privacy"}
      onClose={onClose}
      wide
    >
      <p>
        <strong>{info.stays_on_device}</strong>
      </p>
      <p className="muted">
        Stored encrypted on this device ({info.store}); the key is kept in{" "}
        {info.key_store === "keyring"
          ? "your system keychain"
          : `a private file (${info.key_store})`}
        . Calendars:{" "}
        {info.calendars.length ? info.calendars.join(", ") : "none connected"}.
      </p>
      <fieldset>
        <legend id={`${gid}-work`}>Work you finish</legend>
        {radio("work", work, setWork)}
      </fieldset>
      <fieldset>
        <legend id={`${gid}-meeting`}>Meetings that ended</legend>
        {radio("meeting", meeting, setMeeting)}
      </fieldset>
      <fieldset>
        <legend>Development (training, courses, reading)</legend>
        <label className="choice">
          <input
            type="checkbox"
            checked={development}
            onChange={(e) => setDevelopment(e.target.checked)}
          />
          Send development time to my development project, tagged #development
          (visible to me and my line manager)
        </label>
      </fieldset>
      <p>
        <strong>Personal</strong> (sport, errands, health): never sent. There is
        no way to send a personal item.
      </p>
      <div className="buttons">
        <button
          type="button"
          className="primary"
          onClick={() => void save()}
          data-autofocus
        >
          {firstRun ? "Got it" : "Save"}
        </button>
        {!firstRun ? (
          <button type="button" onClick={onClose}>
            Close
          </button>
        ) : null}
      </div>
      {!firstRun ? (
        <>
          <h3>What has been sent</h3>
          {sent === null ? (
            <p className="muted">Loading…</p>
          ) : sent.length === 0 ? (
            <p className="muted">Nothing sent yet.</p>
          ) : (
            <table>
              <caption className="sr-only">Sent items</caption>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">What</th>
                  <th scope="col">Where</th>
                  <th scope="col">
                    <span className="sr-only">Action</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sent.map((s) => (
                  <tr key={s.client_id}>
                    <td>{clock(s.at)}</td>
                    <td>
                      {s.title} · {hm(s.minutes)}
                    </td>
                    <td>
                      {s.project} {s.server_key ?? "(queued)"}{" "}
                      {s.unsent ? "· taken back" : ""}
                    </td>
                    <td>
                      {s.can_unsend && !s.unsent ? (
                        <button type="button" onClick={() => void unsend(s)}>
                          Unsend<span className="sr-only"> {s.title}</span>
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h3>Erase</h3>
          {confirmErase ? (
            <div className="buttons" style={{ justifyContent: "flex-start" }}>
              <button
                type="button"
                onClick={() => void erase()}
                style={{ color: "var(--danger)" }}
              >
                Yes, erase everything on this device
              </button>
              <button type="button" onClick={() => setConfirmErase(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => setConfirmErase(true)}>
              Erase all local data…
            </button>
          )}
        </>
      ) : null}
    </Dialog>
  );
}

/** Recurring editor: natural text -> a live RRULE preview -> save. Per-series share policy for development series. */
export function RecurringEditor({
  bridge,
  onClose,
  onSaved,
  announce,
}: {
  bridge: PersonalBridge;
  onClose: () => void;
  onSaved: () => void;
  announce: (msg: string, error?: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sendEach, setSendEach] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    if (!text.trim()) {
      setDraft(null);
      return;
    }
    const t = setTimeout(() => {
      void bridge
        .preview(text)
        .then((r) => mine === seq.current && setDraft(r.drafts[0] ?? null));
    }, 150);
    return () => clearTimeout(t);
  }, [text, bridge]);

  const save = async () => {
    try {
      const r = await bridge.recurAdd(text, sendEach);
      announce(`Added ${r.title}: ${r.rule}`);
      onSaved();
      onClose();
    } catch (e) {
      announce((e as Error).message, true);
    }
  };

  return (
    <Dialog title="Repeating item" onClose={onClose}>
      <label style={{ display: "grid", gap: 4 }}>
        Say it in words
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="every weekday 7:00 run 30m"
          data-autofocus
          onKeyDown={(e) => e.key === "Enter" && draft?.rrule && void save()}
        />
      </label>
      <p className="muted">
        Try “Tue/Thu 18:00 Arabic course 1h”, “read 20m daily”, “every 2 weeks
        on friday 15:00 retro 1h”. Add “until dec 31” or “for 4 weeks” to end
        it.
      </p>
      <div role="status" aria-live="polite" style={{ minHeight: "3em" }}>
        {draft ? (
          draft.rrule ? (
            <>
              <strong>{draft.rrule_text}</strong> · {draft.title}
              <br />
              <code>{draft.rrule}</code>
            </>
          ) : (
            <span>No repetition found in this line yet.</span>
          )
        ) : null}
        {draft?.warnings.map((w) => (
          <div key={w}>{w}</div>
        ))}
      </div>
      <label className="choice">
        <input
          type="checkbox"
          checked={sendEach}
          onChange={(e) => setSendEach(e.target.checked)}
        />
        Send each completed instance (development items only)
      </label>
      <div className="buttons">
        <button
          type="button"
          className="primary"
          onClick={() => void save()}
          disabled={!draft?.rrule}
        >
          Save repeating item
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}

const KEYS: [string, string][] = [
  ["N", "New item (jump to quick add)"],
  ["Space", "Start or stop the timer on the selected item"],
  ["C", "Mark the selected item done"],
  ["X", "Skip the selected item"],
  ["S", "Send the selected ready-to-send entry"],
  ["D · W · M", "Day, week or month view"],
  ["↑ ↓", "Move between items"],
  ["E", "End-of-day review"],
  ["R", "Weekly review"],
  ["?", "This list"],
  ["Esc", "Close a dialog"],
];

export function KeyHelp({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Keyboard" onClose={onClose}>
      <table>
        <caption className="sr-only">Keyboard shortcuts</caption>
        <thead>
          <tr>
            <th scope="col">Key</th>
            <th scope="col">Does</th>
          </tr>
        </thead>
        <tbody>
          {KEYS.map(([k, d]) => (
            <tr key={k}>
              <th scope="row">
                <kbd>{k}</kbd>
              </th>
              <td>{d}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="buttons">
        <button
          type="button"
          className="primary"
          onClick={onClose}
          data-autofocus
        >
          Close
        </button>
      </div>
    </Dialog>
  );
}
