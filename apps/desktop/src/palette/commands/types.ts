/** Wire types for management commands (Phase 14). They mirror `tt_client.sidecar_commands` 1:1: the shells never re-implement
 * parsing, validation, role lenses or confirmation rules; they ask the bridge (the sidecar on desktop/mobile, ADR-0038). */

export interface RoleMode {
  mode: string;
  label: string;
}
export interface Chip {
  label: string;
  op: string;
  text: string;
}
export interface OpInfo {
  op: string;
  group: string;
  description: string;
  destructive: boolean;
  preview: boolean;
  first: boolean;
}
/** One row of `pending_approvals` in the ContextPack (a list, never a bare count; the count is `pending_approvals_count`). */
export interface PendingApproval {
  id: string;
  title?: string;
  project_key?: string;
  requested_by?: string;
  requested_at?: string;
}
export interface RoleState {
  role: string;
  modes: RoleMode[];
  chips: Chip[];
  ops: OpInfo[];
  offline_catalogue?: boolean;
  review_queue_count?: number;
  pending_approvals?: PendingApproval[];
  error?: string;
}

/** A `ClientCommand` as JSON. `args._meta` is owned by the sidecar (client_version, mode, role_mode, forced). */
export interface Command {
  client_id: string;
  actor: string;
  at: string;
  op: string;
  args: Record<string, unknown>;
  on_behalf_of?: string | null;
  preview_only?: boolean;
}
export interface EffectRow {
  label: string;
  value: string;
}
export interface Outcome {
  client_id: string;
  op: string;
  args: Record<string, unknown>;
  on_behalf_of?: string | null;
  status: "previewed" | "rejected" | "no_preview" | "offline";
  rows: EffectRow[];
  summary: string;
  errors: { message?: string; code?: string; pointer?: string }[];
  destructive: boolean;
}

export interface Choice {
  value: string;
  label: string;
}
export interface FieldSpec {
  name: string;
  label: string;
  kind: "text" | "enum" | "integer" | "number" | "boolean" | "date" | "json";
  required: boolean;
  multi: boolean;
  description: string;
  picker: "person" | "project" | "team" | "item" | null;
  choices: Choice[];
  strict: boolean;
  default: unknown;
  constraints: Record<string, unknown>;
}
export interface FormSpec {
  op: string;
  description: string;
  destructive: boolean;
  preview: boolean;
  fields: FieldSpec[];
  error?: string;
}
export type Answers = Record<string, string | string[] | boolean>;

export interface Proposal {
  commands: Command[];
  source: string;
  notes: string[];
  unmatched: string[];
  incomplete: {
    op: string;
    args: Record<string, unknown>;
    issues: string[];
    form: FormSpec;
  }[];
  error?: string;
}
export interface SendOutcome {
  status:
    | "sent"
    | "queued"
    | "rejected"
    | "failed"
    | "needs_confirmation"
    | "needs_second_confirmation";
  results?: { client_id?: string; status: string }[];
  rejected?: {
    client_id: string;
    errors: { message?: string; code?: string }[];
  }[];
  error?: string;
}
/** One review-queue row as the sidecar maps it once (`tt_client.rows.ReviewRow.to_doc`): never the raw API field names. */
export interface QueueRow {
  ref: string;
  item_key: string;
  item_title: string;
  reporter?: string | null;
  age?: string;
  kind?: string;
  /** The server's suggested resolution (`suggested.choice`), passed back verbatim to resolve the row. */
  choice: Record<string, unknown> | null;
  choice_label: string | null;
  reason?: string | null;
  /** `INBOX-sara-1 · tidy the ingest retry · @sara · 3 min → WEB [Enter]` */
  line: string;
}
/** One "team now" row (`tt_client.rows.TeamNowRow.to_doc`). */
export interface TeamActivity {
  person: string;
  item_key: string;
  item_title: string;
  project_key?: string | null;
  age?: string;
  /** `@sara · WEB-114 fix the login redirect loop · 12 min` */
  line: string;
}

export interface CommandBridge {
  state(): Promise<RoleState>;
  setRole(mode: string): Promise<RoleState>;
  propose(text: string): Promise<Proposal>;
  fields(op: string): Promise<FormSpec>;
  build(
    op: string,
    answers: Answers,
  ): Promise<{
    command?: Command;
    errors?: { pointer: string; message: string }[];
    error?: string;
  }>;
  preview(commands: Command[]): Promise<{ outcomes: Outcome[] }>;
  send(
    commands: Command[],
    opts: { confirmed: boolean; second_confirmed?: boolean; force?: boolean },
  ): Promise<SendOutcome>;
  queue(): Promise<{ rows: QueueRow[]; error?: string }>;
  resolve(
    rows: { ref: string; choice: Record<string, unknown> }[],
  ): Promise<SendOutcome>;
  teamNow(): Promise<{ activities: TeamActivity[]; error?: string }>;
}
