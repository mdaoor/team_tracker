/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Hand-written response shapes for the endpoints whose OpenAPI response is an untyped object (`{ [key: string]: unknown }`).
 * Each interface names the fields the console reads and keeps an index signature, so a field the server adds later is still
 * reachable without a cast. `Json` is for genuinely free-form documents (CEL policy bodies, filter clauses, template bodies).
 */

/** A free-form JSON object: policy definitions, filter clauses, slide configs, template bodies. */
export type Json = Record<string, any>;

// -- session ---------------------------------------------------------------------------------------------------------
export interface Me extends Json {
  can?: Record<string, boolean>;
  nav?: Record<string, boolean>;
  is_admin?: boolean;
}
export interface Workspace extends Json {
  slug: string;
  name: string;
}
export interface AuthProviders extends Json {
  dev?: boolean;
  ldap?: boolean;
}

// -- items -----------------------------------------------------------------------------------------------------------
export interface Item extends Json {
  id?: string;
  key: string;
  title: string;
  type?: string;
  state?: string;
}
export interface ItemEvent extends Json {
  occurred_at?: string;
  event_type?: string;
}
export interface Placement extends Json {
  process?: string;
  stage?: string;
}
export interface Violation extends Json {
  id: string;
  status?: string;
  severity?: string;
  policy?: string;
}
export interface ItemsResult extends Json {
  items?: Item[];
}
export interface BoardColumn extends Json {
  state?: string;
  stage?: string;
  items: Item[];
}
export interface BoardResult extends Json {
  columns?: BoardColumn[];
}
export interface AggregateResult extends Json {
  rows: Json[];
  cols: string[];
}
export interface CalendarResult extends Json {
  days?: Record<string, Item[]>;
}
export interface GanttResult extends Json {
  rows?: GanttRow[];
  deps?: GanttDep[];
  milestones?: Milestone[];
}

// -- setup -----------------------------------------------------------------------------------------------------------
export interface Project extends Json {
  id?: string;
  key: string;
  name?: string;
}
export interface Product extends Json {
  id?: string;
  key: string;
  name?: string;
  parent_product_id?: string | null;
}
export interface Stage extends Json {
  key: string;
  name?: string;
}
export interface Process extends Json {
  key: string;
  stages?: Stage[];
}
export interface Person extends Json {
  id: string;
  name?: string;
}
export interface Team extends Json {
  id?: string;
  key?: string;
  name?: string;
}
export interface Link extends Json {
  target_type?: string;
}

// -- governance ------------------------------------------------------------------------------------------------------
export interface PolicyParam extends Json {
  name: string;
  type?: string;
}
export interface PolicyTemplate extends Json {
  key: string;
  params?: PolicyParam[];
}
export interface Policy extends Json {
  key?: string;
  definition?: Json;
}
export interface PolicyVersion extends Json {
  definition?: Json;
}
export interface Habit extends Json {
  id: string;
}
export interface WeightScheme extends Json {
  id: string;
  name?: string;
}
export interface Insight extends Json {
  id: string;
  project?: string;
  body?: string;
}
export interface Digest extends Json {
  daily_logs?: Json[];
  events?: Json[];
  notes?: Json[];
  insights?: Json[];
}
export interface ContextSchema extends Json {
  functions?: Json[];
}

// -- present ---------------------------------------------------------------------------------------------------------
export interface Slide extends Json {
  id: string;
  kind: string;
  title?: string;
  config: Json;
}
export interface Presentation extends Json {
  id: string;
  title?: string;
  slides?: Slide[];
}
export interface TemplateParam extends Json {
  name: string;
}
export interface TemplateBody extends Json {
  slides?: Json[];
  params?: TemplateParam[];
}
export interface PresentationTemplate extends Json {
  key: string;
  params: TemplateParam[];
}

// -- plan ------------------------------------------------------------------------------------------------------------
export interface PlanRow extends Json {
  key: string;
  level?: number;
  planned_start?: string | null;
  planned_end?: string | null;
}
export interface Plan extends Json {
  project: Project;
  phases?: Json[];
  milestones: Json[];
}

// -- review queue ----------------------------------------------------------------------------------------------------
export interface ReviewRow extends Json {
  ref: string;
}
export type ReviewChoice = Json;
export type Row = Json;

// -- views and gantt -------------------------------------------------------------------------------------------------
export interface SavedView extends Json {
  id: string;
  name: string;
  filter: Json;
  layout?: string;
  weight_scheme_id?: string | null;
}
export interface GanttRow extends Json {
  planned_start?: string | null;
  planned_end?: string | null;
  baseline?: { planned_start: string; planned_end: string } | null;
}
export interface GanttDep extends Json {
  from: string;
  to: string;
}
export interface Milestone extends Json {
  due: string;
}
