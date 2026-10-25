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
export interface RoleState {
  role: string;
  modes: RoleMode[];
  chips: Chip[];
  ops: OpInfo[];
  offline_catalogue?: boolean;
  review_queue_count?: number;
  pending_approvals?: { id: string; title?: string }[];
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
export interface QueueRow {
  id: string;
  summary?: string;
  title?: string;
  default_choice?: string;
  choices?: string[];
}
export interface TeamActivity {
  person?: string | { handle?: string };
  item?: { key?: string; title?: string };
  key?: string;
  title?: string;
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
  resolve(rows: { id: string; choice: string }[]): Promise<SendOutcome>;
  teamNow(): Promise<{ activities: TeamActivity[]; error?: string }>;
}
