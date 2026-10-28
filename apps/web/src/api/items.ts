import { api, w } from "./http";
import type {
  AggregateResult,
  BoardResult,
  CalendarResult,
  GanttResult,
  Item,
  ItemEvent,
  ItemsResult,
  Json,
  Placement,
  Violation,
} from "./types";

type List<T> = { data?: T[] };

export const getItem = (ws: string, key: string) => api.getWithEtag<Item>(w(ws, `/items/${key}`));
export const itemProvenance = (ws: string, key: string) =>
  api.get<Json>(w(ws, `/items/${key}/provenance`));
export const itemPlacements = (ws: string, key: string) =>
  api.get<List<Placement>>(w(ws, `/items/${key}/placements`));
export const itemEvents = (ws: string, key: string) =>
  api.get<List<ItemEvent>>(w(ws, `/items/${key}/events`));
export const itemViolations = (ws: string, itemId: string | undefined) =>
  api.get<List<Violation>>(w(ws, "/violations"), { target_id: itemId, limit: 100 });

/** GET with the item's ETag, then PATCH with `If-Match` (the plan page's date edit). */
export async function patchItemFields(ws: string, key: string, fields: Json): Promise<Item> {
  const cur = await getItem(ws, key);
  return api.patch<Item>(w(ws, `/items/${key}`), fields, { "If-Match": cur.etag ?? "*" });
}

export const setItemState = (ws: string, key: string, state: string) =>
  api.post(w(ws, `/items/${key}/state`), { state });
export const setItemStage = (ws: string, key: string, process: string, stage: string) =>
  api.put(w(ws, `/items/${key}/stage`), { process, stage });
export const setItemWeight = (ws: string, key: string, scheme: string, value: number) =>
  api.put(w(ws, `/items/${key}/weights/${scheme}`), { value });

export const queryItems = (ws: string, body: unknown) =>
  api.post<ItemsResult>(w(ws, "/query/items"), body);
export const queryGantt = (ws: string, body: unknown) =>
  api.post<GanttResult>(w(ws, "/query/gantt"), body);
export const queryAggregate = (ws: string, body: unknown) =>
  api.post<AggregateResult>(w(ws, "/query/aggregate"), body);
export const queryCalendar = (ws: string, body: unknown) =>
  api.post<CalendarResult>(w(ws, "/query/calendar"), body);
/** Board / stage-board columns (`path` is the board endpoint for the chosen layout). */
export const getBoard = (ws: string, path: string, filter: string) =>
  api.get<BoardResult>(w(ws, path), { filter });
export const getNow = (ws: string) => api.get<List<Item>>(w(ws, "/now"));
