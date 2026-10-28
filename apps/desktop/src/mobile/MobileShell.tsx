import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Dialog } from "../personal/Dialog";
import { PersonalApp } from "../personal/PersonalApp";
import type { LocalBridge, MobileSettings } from "./localBridge";

type Delivery = { text: string; error: boolean } | null;

/** Mobile Lite: the personal planner views on a phone, around the browser-local bridge. This bar adds only what the desktop gets from the
 * sidecar and the OS: whether the phone is online, how many sends wait for delivery, and the few settings delivery needs. */
export function MobileShell({
  bridge,
  pollMs = 30000,
  deliverEveryMs = 60000,
}: {
  bridge: LocalBridge;
  pollMs?: number;
  deliverEveryMs?: number;
}) {
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );
  const [pending, setPending] = useState(bridge.pending());
  const [delivery, setDelivery] = useState<Delivery>(null);
  const [dialog, setDialog] = useState(false);
  const busy = useRef(false);

  const deliver = useCallback(
    async (manual: boolean) => {
      if (busy.current) return;
      const s = bridge.settings();
      if (!bridge.pending()) {
        setPending(0);
        if (manual) setDelivery({ text: "Nothing is waiting.", error: false });
        return;
      }
      if (!s.workspace || !s.handle) {
        if (manual)
          setDelivery({
            text: "Set your @handle and workspace in Phone settings first.",
            error: true,
          });
        return;
      }
      busy.current = true;
      try {
        const r = await bridge.deliver();
        setPending(bridge.pending());
        if (r.sent)
          setDelivery({
            text: `Sent ${r.sent} to Team Tracker.`,
            error: false,
          });
        else if (manual && r.offline)
          setDelivery({
            text: "No connection to the server: kept for later.",
            error: true,
          });
        else if (r.failed)
          setDelivery({
            text: `${r.failed} send(s) were refused by the server; check the token in Phone settings.`,
            error: true,
          });
      } finally {
        busy.current = false;
      }
    },
    [bridge],
  );

  useEffect(
    () => bridge.subscribe(() => setPending(bridge.pending())),
    [bridge],
  );
  useEffect(() => {
    const up = () => {
      setOnline(true);
      void deliver(false);
    };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, [deliver]);
  useEffect(() => {
    const t = setInterval(() => {
      setPending(bridge.pending());
      if (deliverEveryMs && navigator.onLine) void deliver(false);
    }, pollMs || 1000);
    return () => clearInterval(t);
  }, [bridge, deliver, pollMs, deliverEveryMs]);

  return (
    <>
      <div className="pt pt-mobilebar" role="region" aria-label="Phone status">
        <span className={online ? "pt-online" : "pt-offline"}>
          {online ? "Online" : "Offline: everything is kept on this phone"}
        </span>
        <span aria-live="polite">
          {pending ? `${pending} waiting to send` : "Nothing waiting"}
        </span>
        <button type="button" onClick={() => void deliver(true)}>
          Send now
        </button>
        <button type="button" onClick={() => setDialog(true)}>
          Phone settings
        </button>
        {delivery ? (
          <span
            role="status"
            className={delivery.error ? "pt-error" : undefined}
          >
            {delivery.text}
          </span>
        ) : null}
      </div>
      <PersonalApp bridge={bridge} pollMs={pollMs} />
      {dialog ? (
        <div className="pt">
          <PhoneSettings
            bridge={bridge}
            onClose={() => {
              setDialog(false);
              setPending(bridge.pending());
            }}
          />
        </div>
      ) : null}
    </>
  );
}

function PhoneSettings({
  bridge,
  onClose,
}: {
  bridge: LocalBridge;
  onClose: () => void;
}) {
  const [s, setS] = useState<MobileSettings>(bridge.settings());
  const [projects, setProjects] = useState(s.projects.join(", "));
  const save = () => {
    bridge.saveSettings({
      ...s,
      handle: s.handle.trim().replace(/^(?!@)(.+)$/, "@$1"),
      server: s.server.trim(),
      workspace: s.workspace.trim(),
      projects: projects.split(/[\s,]+/).filter(Boolean),
    });
    onClose();
  };
  const field = (
    label: string,
    key: keyof MobileSettings,
    hint?: string,
    type = "text",
  ) => (
    <Field
      label={label}
      hint={hint}
      type={type}
      value={String(s[key])}
      onChange={(v) => setS({ ...s, [key]: v })}
    />
  );
  return (
    <Dialog title="Phone settings" onClose={onClose} wide>
      <p className="muted">
        Mobile Lite keeps your plan in this browser,{" "}
        <strong>not encrypted</strong>. Anyone who can open this browser profile
        can read it. Personal items are never sent. Calendars, repeating items
        and suggestions are desktop features for now.
      </p>
      {field("Your handle", "handle", "For example @mahmoud")}
      {field(
        "Workspace",
        "workspace",
        "The workspace slug in the web console URL",
      )}
      {field(
        "Server",
        "server",
        "Leave empty when this page is served by your Team Tracker server",
      )}
      {field(
        "API token",
        "token",
        "A capture token. Kept in this browser in clear text; revoke it in the web console if the phone is lost.",
        "password",
      )}
      <Field
        label="Your projects"
        hint="Keys offered when an item has none, for example WEB, DATA, OPS"
        value={projects}
        onChange={setProjects}
        capitalize="characters"
      />
      <div className="buttons">
        <button type="button" className="primary" onClick={save} data-autofocus>
          Save
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
  type = "text",
  capitalize = "off",
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  capitalize?: "off" | "characters";
}) {
  const id = useId();
  return (
    <div style={{ display: "grid", gap: 4, marginBottom: 12 }}>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
        autoComplete="off"
        autoCapitalize={capitalize}
        spellCheck={false}
      />
      {hint ? (
        <span id={`${id}-hint`} className="muted">
          {hint}
        </span>
      ) : null}
    </div>
  );
}
