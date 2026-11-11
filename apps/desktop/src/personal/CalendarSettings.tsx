import { useCallback, useEffect, useId, useState } from "react";
import { Dialog } from "./Dialog";
import type { CalendarSettings as Info, PersonalBridge } from "./types";

const PROVIDER_LABEL: Record<string, string> = {
  google: "Google",
  microsoft: "Outlook / Teams",
};

/** Which calendars are read, the never-offer lists, and a button to re-list the calendars (plan 15 M7/M8). Everything goes through the
 * bridge: switching a calendar off removes its unsent meetings from the plan, switching it on brings them back on the next sync. */
export function CalendarSettingsDialog({
  bridge,
  onClose,
  onChanged,
  announce,
}: {
  bridge: PersonalBridge;
  onClose: () => void;
  onChanged: () => void;
  announce: (msg: string, error?: boolean) => void;
}) {
  const [info, setInfo] = useState<Info | null>(null);
  const [status, setStatus] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const gid = useId();

  const fail = useCallback(
    (e: unknown) => {
      const m = e instanceof Error ? e.message : String(e);
      setStatus(m);
      announce(m, true);
    },
    [announce],
  );
  useEffect(() => {
    void bridge.calSettings().then(setInfo).catch(fail);
  }, [bridge, fail]);

  const toggle = async (id: string, excluded: boolean) => {
    try {
      setInfo(await bridge.calExclude(id, excluded));
      setStatus(
        excluded
          ? "Calendar switched off: its meetings left your plan."
          : "Calendar switched on: its meetings come back with the next sync.",
      );
      onChanged();
    } catch (e) {
      fail(e);
    }
  };
  const refresh = async () => {
    setBusy(true);
    try {
      const { refreshed, ...rest } = await bridge.calRefresh();
      setInfo(rest);
      const lines = refreshed.map((x) =>
        x.error
          ? `${x.provider}: ${x.error}`
          : `${x.provider}: ${x.added.length} added${x.added.length ? ` (${x.added.join(", ")})` : ""}, ${x.removed.length} removed${x.removed.length ? ` (${x.removed.join(", ")})` : ""}`,
      );
      setStatus(lines.join(" · ") || "Nothing to refresh.");
      onChanged();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const suppress = async (
    kind: "attendee" | "domain",
    v: string,
    remove?: boolean,
  ) => {
    try {
      setInfo(await bridge.calSuppress(kind, v, remove));
      setStatus(
        remove ? `${v} can be offered again.` : `${v} will not be offered.`,
      );
      if (!remove) setValue("");
      onChanged();
    } catch (e) {
      fail(e);
    }
  };
  const add = () => {
    const v = value.trim();
    if (v)
      void suppress(
        v.replace(/^@/, "").includes("@") ? "attendee" : "domain",
        v,
      );
  };

  const providers = info
    ? [...new Set(info.calendars.map((c) => c.provider))]
    : [];
  return (
    <Dialog title="Calendars" onClose={onClose} wide>
      {info === null ? (
        <p className="muted">Loading…</p>
      ) : info.calendars.length === 0 ? (
        <p>
          No calendar connected yet. Connect Google or Outlook with{" "}
          <code>tt cal connect google</code> or{" "}
          <code>tt cal connect microsoft</code>; the calendars appear here after
          the first sync.
        </p>
      ) : (
        providers.map((p) => (
          <fieldset key={p}>
            <legend>{PROVIDER_LABEL[p] ?? p}</legend>
            {info.calendars
              .filter((c) => c.provider === p)
              .map((c) => (
                <label key={c.id} className="choice">
                  <input
                    type="checkbox"
                    checked={!c.excluded && c.selected}
                    disabled={!c.selected && !c.excluded}
                    onChange={(e) => void toggle(c.id, !e.target.checked)}
                  />
                  {c.name}
                  {c.primary ? " (main)" : ""}
                  {c.excluded ? " — not read" : ""}
                  {!c.selected && !c.excluded
                    ? ` — hidden by ${PROVIDER_LABEL[p] ?? p}; show it there to read it`
                    : ""}
                </label>
              ))}
            <p className="muted">
              List read{" "}
              {info.refreshed_at[p]
                ? (info.refreshed_at[p] ?? "").slice(0, 16).replace("T", " ")
                : "never"}
              ; read again every{" "}
              {info.refresh_hours
                ? `${info.refresh_hours} h`
                : "— (only on demand)"}
              .
            </p>
          </fieldset>
        ))
      )}
      <div className="buttons" style={{ justifyContent: "flex-start" }}>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={busy || !info?.calendars.length}
        >
          Refresh the list
        </button>
      </div>

      <fieldset>
        <legend>Never offer to send</legend>
        <p className="muted">
          Meetings with these people or companies stay in your plan but never
          appear in Ready to send.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <label htmlFor={`${gid}-sup`} className="sr-only">
            Never offer meetings with
          </label>
          <input
            id={`${gid}-sup`}
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder="sara@acme.com or globex.com"
            style={{ flex: 1 }}
            aria-describedby={`${gid}-hint`}
          />
          <button type="button" onClick={add}>
            Suppress
          </button>
        </div>
        <p id={`${gid}-hint`} className="muted">
          An e-mail address, or a company domain such as globex.com.
        </p>
        <ul aria-label="Suppressed">
          {info?.suppress.attendees.map((a) => (
            <li key={a}>
              <span>{a}</span>{" "}
              <button
                type="button"
                className="quiet"
                onClick={() => void suppress("attendee", a, true)}
              >
                Remove<span className="sr-only"> {a}</span>
              </button>
            </li>
          ))}
          {info?.suppress.domains.map((d) => (
            <li key={d}>
              <span>{d}</span>{" "}
              <button
                type="button"
                className="quiet"
                onClick={() => void suppress("domain", d, true)}
              >
                Remove<span className="sr-only"> {d}</span>
              </button>
            </li>
          ))}
        </ul>
      </fieldset>

      <div role="status" aria-live="polite" style={{ minHeight: "1.5em" }}>
        {status}
      </div>
      <div className="buttons">
        <button type="button" className="primary" onClick={onClose}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
