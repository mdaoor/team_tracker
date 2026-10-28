import { BridgeError, type PersonalBridge } from "./types";

/** The real bridge: one Tauri command, `tt_personal`, forwards `{command, args}` to `tt-sidecar` (`tt sidecar <command> '<json>'`), which
 * answers one JSON document. Errors arrive as `{error: {code, message, alternatives?}}` and become `BridgeError`s. Only usable inside the
 * Tauri webview; the dynamic import keeps the web build free of the Tauri packages. Tests and `pnpm dev` use `demoBridge` instead. */
type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

async function invoke(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<unknown> {
  const mod = "@tauri-apps/api/core";
  const { invoke: inv } = (await import(/* @vite-ignore */ mod)) as {
    invoke: Invoke;
  };
  return inv(cmd, args);
}

export async function personalCall<T>(
  command: string,
  args: Record<string, unknown> = {},
  call: (cmd: string, a?: Record<string, unknown>) => Promise<unknown> = invoke,
): Promise<T> {
  const out = (await call("tt_personal", { command, args })) as Record<
    string,
    unknown
  >;
  const err = out?.error as
    { code: string; message: string; alternatives?: string[] } | undefined;
  if (err) throw new BridgeError(err.code, err.message, err.alternatives ?? []);
  return out as T;
}

export function makeBridge(
  call: (cmd: string, a?: Record<string, unknown>) => Promise<unknown> = invoke,
): PersonalBridge {
  const c = <T>(command: string, args: Record<string, unknown> = {}) =>
    personalCall<T>(command, args, call);
  return {
    day: (date) => c("personal_day", { date }),
    week: (date) => c("personal_week", { date }),
    month: (year, month) => c("personal_month", { year, month }),
    preview: (line) => c("personal_preview", { line }),
    add: (line) => c("personal_add", { line }),
    recurAdd: (text, sendEach) =>
      c("personal_recur_add", { text, send_each: !!sendEach }),
    complete: (id, minutes) => c("personal_complete", { id, minutes }),
    skip: (id) => c("personal_skip", { id }),
    reopen: (id) => c("personal_reopen", { id }),
    remove: (id) => c("personal_delete", { id }),
    reschedule: (id, start) => c("personal_reschedule", { id, start }),
    setCategory: (id, category) => c("personal_set_category", { id, category }),
    timerStart: (arg) => c("personal_timer_start", arg),
    timerStop: () => c("personal_timer_stop"),
    timerStatus: () => c("personal_timer_status"),
    tray: () => c("personal_tray"),
    send: (arg) => c("personal_send", arg),
    notWork: (id) => c("personal_not_work", { id }),
    unsend: (id) => c("personal_unsend", { id }),
    rememberShare: (category, mode) =>
      c("personal_remember_share", { category, mode }),
    eod: (date) => c("personal_eod", { date }),
    eodSendAll: (date) => c("personal_eod_send_all", { date }),
    eodConfirm: (date) => c("personal_eod_confirm", { date }),
    weeklyReview: (date) => c("personal_weekly_review", { date }),
    developmentSummary: (date) => c("personal_development_summary", { date }),
    deliver: () => c("personal_deliver"),
    privacy: () => c("personal_privacy"),
    firstRun: (arg) => c("personal_first_run", arg),
    sent: () => c("personal_sent"),
    eraseLocal: () => c("personal_erase_local"),
  };
}

export const personalSidecarBridge: PersonalBridge = makeBridge();
