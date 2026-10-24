import type { Bridge } from "./types";

/** The real bridge: Tauri commands that talk to the `tt_client` Python sidecar (ADR-0016). It is only usable inside the Tauri
 * webview; the unit tests use a scripted bridge instead. The import is dynamic so the web build never needs the Tauri packages. */
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

export const sidecarBridge: Bridge = {
  parse: (text, opts) =>
    invoke("tt_parse", { text, ...opts }) as ReturnType<Bridge["parse"]>,
  pack: () => invoke("tt_pack") as ReturnType<Bridge["pack"]>,
  send: (batch, opts) =>
    invoke("tt_send", { batch, ...opts }) as ReturnType<Bridge["send"]>,
  buildManual: (answers) =>
    invoke("tt_build_manual", { answers }) as ReturnType<Bridge["buildManual"]>,
  outbox: () => invoke("tt_outbox") as ReturnType<Bridge["outbox"]>,
  resend: (id) => invoke("tt_resend", { id }) as ReturnType<Bridge["resend"]>,
  discard: (id) => invoke("tt_discard", { id }) as Promise<void>,
  openInEditor: (batch) =>
    invoke("tt_open_editor", { batch }) as ReturnType<Bridge["openInEditor"]>,
  active: () => invoke("tt_active") as ReturnType<Bridge["active"]>,
};
