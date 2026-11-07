import type { Active } from "./types";

/** Tray timer state. The server's active activity wins when online; offline, the last local `start` is used. Starting another
 * item pauses the first (the server rule, mirrored here so the tray is right before the server answers). */
export interface TimerState {
  current: Active | null;
  accumulatedMs: number; // time already banked on `current` before the latest (re)start
  since: number | null; // epoch ms of the latest (re)start; null while paused
}

export const idle: TimerState = {
  current: null,
  accumulatedMs: 0,
  since: null,
};

export function fromServer(a: Active | null, now: number): TimerState {
  if (!a) return idle;
  const t0 = Date.parse(a.started_at);
  return a.paused
    ? { current: a, accumulatedMs: Math.max(0, now - t0), since: null }
    : { current: a, accumulatedMs: 0, since: t0 };
}

export function start(
  s: TimerState,
  item: Pick<Active, "key" | "title">,
  now: number,
): TimerState {
  if (s.current?.key === item.key && s.since !== null) return s;
  return {
    current: { ...item, started_at: new Date(now).toISOString() },
    accumulatedMs: 0,
    since: now,
  };
}
export function pause(s: TimerState, now: number): TimerState {
  if (!s.current || s.since === null) return s;
  return {
    ...s,
    accumulatedMs: s.accumulatedMs + (now - s.since),
    since: null,
    current: { ...s.current, paused: true },
  };
}
export function resume(s: TimerState, now: number): TimerState {
  if (!s.current || s.since !== null) return s;
  return { ...s, since: now, current: { ...s.current, paused: false } };
}
export function done(): TimerState {
  return idle;
}
export function elapsedMs(s: TimerState, now: number): number {
  return s.accumulatedMs + (s.since !== null ? Math.max(0, now - s.since) : 0);
}
export function format(ms: number): string {
  const m = Math.floor(ms / 60000);
  return m < 60
    ? `${m}m`
    : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
