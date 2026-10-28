import { api, w } from "./http";
import type { Habit, Insight, Json, Policy, WeightPreview, WeightScheme } from "./types";

export const activatePolicy = (ws: string, key: string, on: boolean) =>
  api.post(w(ws, `/policies/${key}:${on ? "activate" : "deactivate"}`));
export const checkPolicy = (ws: string, body: unknown) =>
  api.post<Json>(w(ws, "/policies:check"), body);
export const createPolicy = (ws: string, body: unknown) =>
  api.post<Policy>(w(ws, "/policies"), body);
export const updatePolicy = (ws: string, key: string, body: unknown) =>
  api.patch<Policy>(w(ws, `/policies/${key}`), body);
export const testPolicy = (ws: string, key: string) =>
  api.post<Json>(w(ws, `/policies/${key}:test`), { window: "7d" });
export const activatePolicyForced = (ws: string, key: string, force: boolean) =>
  api.post<Json>(w(ws, `/policies/${key}:activate${force ? "?force=true" : ""}`));
export const authorPolicy = (ws: string, request: string) =>
  api.post<Json>(w(ws, "/policies:author"), { request });

export const violationAction = (ws: string, id: string, act: string, body: unknown) =>
  api.post(w(ws, `/violations/${id}:${act}`), body);
export const patchHabit = (ws: string, id: string, body: unknown) =>
  api.patch<Habit>(w(ws, `/habits/${id}`), body);
export const markNotificationRead = (ws: string, id: string) =>
  api.post(w(ws, `/notifications/${id}:read`));
export const reviewDailyLog = (ws: string, id: string) =>
  api.post(w(ws, `/daily-logs/${id}/review`));

export const previewWeightScheme = (ws: string, formula: Json) =>
  api.post<WeightPreview>(w(ws, "/weight-schemes:preview"), { formula, sample: 20 });
export const createWeightScheme = (ws: string, name: string, formula: Json) =>
  api.post<WeightScheme>(w(ws, "/weight-schemes"), { name, formula });
export const recomputeWeightScheme = (ws: string, id: string) =>
  api.post(w(ws, `/weight-schemes/${id}/recompute`));

export const setInsightStatus = (ws: string, id: string, status: string) =>
  api.patch<Insight>(w(ws, `/insights/${id}`), { status });
export const generateInsights = (ws: string, scope: string) =>
  api.post(w(ws, "/insights:generate"), { scope });
