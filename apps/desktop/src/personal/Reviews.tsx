import { useState } from "react";
import { Dialog } from "./Dialog";
import { CATEGORY_LABEL, dayLong, hm } from "./format";
import { Meter } from "./Today";
import {
  CATEGORIES,
  type EndOfDay,
  type PersonalBridge,
  type WeeklyReview as Weekly,
} from "./types";

/** End-of-day card: hours by category, items done, the unsent work items with [Send all] [Pick] [Skip]; the server DailyLog is confirmed
 * only after something was sent. */
export function EndOfDayCard({
  data,
  bridge,
  onClose,
  onChanged,
  announce,
}: {
  data: EndOfDay;
  bridge: PersonalBridge;
  onClose: () => void;
  onChanged: () => void;
  announce: (msg: string, error?: boolean) => void;
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [picking, setPicking] = useState(false);
  const [sentSome, setSentSome] = useState(data.sent_count > 0);
  const [confirmed, setConfirmed] = useState<string | null>(null);

  const sendAll = async () => {
    const r = await bridge.eodSendAll(data.date);
    setSentSome(r.sent.length > 0 || sentSome);
    announce(
      `Sent ${r.sent.length}${r.needs_project.length ? `; ${r.needs_project.length} need a project in the tray` : ""}`,
    );
    onChanged();
  };
  const sendPicked = async () => {
    let n = 0;
    for (const e of data.unsent.filter((u) => picked.has(u.id))) {
      try {
        await bridge.send({ id: e.id });
        n++;
      } catch (err) {
        announce((err as Error).message, true);
      }
    }
    setSentSome(n > 0 || sentSome);
    announce(`Sent ${n}`);
    onChanged();
  };
  const confirm = async () => {
    const r = await bridge.eodConfirm(data.date);
    setConfirmed(
      r.sentence ??
        (r.confirmed
          ? `Daily log confirmed (${hm(r.minutes_total)})`
          : (r.reason ?? "Nothing to confirm")),
    );
  };

  return (
    <Dialog title={`End of day · ${dayLong(data.date)}`} onClose={onClose}>
      <table>
        <caption className="sr-only">Hours by category</caption>
        <thead>
          <tr>
            <th scope="col">Category</th>
            <th scope="col">Done</th>
            <th scope="col">Planned</th>
          </tr>
        </thead>
        <tbody>
          {CATEGORIES.map((c) => (
            <tr key={c}>
              <th scope="row">{CATEGORY_LABEL[c]}</th>
              <td>{data.hours_by_category[c]} h</td>
              <td>{data.planned_by_category[c]} h</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        {data.done_count} done · {data.open_count} still open ·{" "}
        {data.sent_count} sent
      </p>
      <h3>Not sent yet ({data.unsent.length})</h3>
      {data.unsent.length === 0 ? (
        <p className="muted">Everything that is work has been sent.</p>
      ) : (
        <ul className="pt-list">
          {data.unsent.map((u) => (
            <li key={u.id} className="pt-card">
              {picking ? (
                <label className="choice">
                  <input
                    type="checkbox"
                    checked={picked.has(u.id)}
                    onChange={(e) => {
                      const next = new Set(picked);
                      if (e.target.checked) next.add(u.id);
                      else next.delete(u.id);
                      setPicked(next);
                    }}
                  />
                  {u.label}
                </label>
              ) : (
                <span className="label">{u.label}</span>
              )}
              {u.needs_project ? (
                <span className="muted">
                  needs a project: choose it in the tray
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {confirmed ? <p role="status">{confirmed}</p> : null}
      <div className="buttons">
        {data.unsent.length ? (
          <>
            <button
              type="button"
              className="primary"
              onClick={() => void sendAll()}
              data-autofocus
            >
              Send all
            </button>
            {picking ? (
              <button
                type="button"
                onClick={() => void sendPicked()}
                disabled={picked.size === 0}
              >
                Send picked ({picked.size})
              </button>
            ) : (
              <button type="button" onClick={() => setPicking(true)}>
                Pick
              </button>
            )}
          </>
        ) : null}
        {sentSome ? (
          <button
            type="button"
            onClick={() => void confirm()}
            className={data.unsent.length ? "" : "primary"}
            data-autofocus={data.unsent.length ? undefined : true}
          >
            Confirm daily log
          </button>
        ) : null}
        <button type="button" onClick={onClose}>
          {data.unsent.length ? "Skip" : "Close"}
        </button>
      </div>
    </Dialog>
  );
}

/** Weekly review: planned vs done, development hours, meeting load, habit streaks; optionally share a development summary. */
export function WeeklyReviewCard({
  data,
  bridge,
  onClose,
  announce,
}: {
  data: Weekly;
  bridge: PersonalBridge;
  onClose: () => void;
  announce: (msg: string, error?: boolean) => void;
}) {
  const [share, setShare] = useState(false);
  const save = async () => {
    if (share) {
      try {
        const r = await bridge.developmentSummary(data.week_start);
        announce(`Queued: ${r.note}`);
      } catch (e) {
        announce((e as Error).message, true);
        return;
      }
    }
    onClose();
  };
  return (
    <Dialog
      title={`Weekly review · week of ${data.week_start}`}
      onClose={onClose}
    >
      <p>
        Planned {data.planned_h} h, done {data.done_h} h ({data.completion_pct}{" "}
        %)
      </p>
      <Meter
        value={data.done_h * 60}
        max={data.planned_h * 60}
        label="Done against planned"
      />
      <p>
        Development <strong>{data.development_h} h</strong> · Meetings{" "}
        <strong>{data.meetings_h} h</strong> ({data.meeting_load_pct} % of your
        capacity)
      </p>
      {data.development_items.length ? (
        <ul>
          {data.development_items.map((d) => (
            <li key={d.title}>
              {d.title}: {d.hours} h
            </li>
          ))}
        </ul>
      ) : null}
      <h3>Streaks</h3>
      {data.streaks.length === 0 ? (
        <p className="muted">No recurring habits yet. Try “read 20m daily”.</p>
      ) : (
        <ul>
          {data.streaks.map((s) => (
            <li key={s.series_id}>
              {s.title}: {s.streak} in a row ({s.done} of {s.of})
            </li>
          ))}
        </ul>
      )}
      <label className="choice">
        <input
          type="checkbox"
          checked={share}
          onChange={(e) => setShare(e.target.checked)}
        />
        Share a development summary with my manager (a note on the development
        project)
      </label>
      <div className="buttons">
        <button
          type="button"
          className="primary"
          onClick={() => void save()}
          data-autofocus
        >
          {share ? "Share and close" : "Close"}
        </button>
      </div>
    </Dialog>
  );
}
