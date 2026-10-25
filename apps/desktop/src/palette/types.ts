/** Wire types shared by the palette, the manual form and the outbox list. They mirror `tt_client` (Python) 1:1: the shells never
 * re-implement parsing; they ask the bridge, which is the sidecar on desktop/mobile (ADR-0016) or a test double. */

export interface PreviewRow {
  label: string;
  value: string;
  cue: string; // "" | "✓ matched" | "72 %" | "suggested" | "ask" | "new"
}
export interface PreviewCard {
  action: string;
  rows: PreviewRow[];
}
export interface Parsed {
  cards: PreviewCard[];
  source: "llm" | "structured";
  batch: Record<string, unknown>; // the ClientBatch JSON, byte-compatible with the CLI's files
  notes: string[];
}
export interface OutboxEntry {
  id: string;
  status: "pending" | "sent" | "failed";
  summary: string;
  error?: string;
  jira_key?: string;
  path?: string;
}
export interface Pack {
  projects: {
    key: string;
    name: string;
    jira?: string | null;
    jira_auto_send?: boolean;
  }[];
  people: { handle: string; name: string }[];
  tags: string[];
  my_open_items: { key: string; title: string; project: string }[];
  priorities?: string[];
  manual_mode?: {
    required_fields: string[];
    per_project?: Record<string, string[]>;
  };
}
export interface SendResult {
  status: "sent" | "queued" | "failed";
  results: {
    status: string;
    item?: { key?: string; title?: string };
    jira?: { key?: string };
    warnings?: string[];
  }[];
}
export interface Active {
  key: string;
  title: string;
  started_at: string; // ISO
  paused?: boolean;
}

/** Everything the UI may do. `parse` is cancellable by the caller ignoring a stale promise; `enter` must never wait on it. */
export interface Bridge {
  parse(text: string, opts: { llm: boolean }): Promise<Parsed>;
  pack(): Promise<Pack | null>;
  /** Writes the outbox file first, then tries to send (so nothing is lost offline). */
  send(
    batch: Record<string, unknown>,
    opts: { jira: boolean },
  ): Promise<SendResult>;
  buildManual(
    answers: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
  outbox(): Promise<OutboxEntry[]>;
  resend(id: string): Promise<SendResult>;
  discard(id: string): Promise<void>;
  openInEditor(
    batch: Record<string, unknown>,
  ): Promise<Record<string, unknown>>; // ⌘E: resolves with the re-validated edit
  active(): Promise<Active | null>;
}
