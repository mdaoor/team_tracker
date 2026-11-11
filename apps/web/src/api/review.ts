import { api, w } from "./http";
import type { Json, ReviewChoice } from "./types";

export const resolveReview = (ws: string, ref: string, choice: ReviewChoice) =>
  api.post<Json>(w(ws, "/review-queue/resolve"), { ref, choice });
export const undoReview = (ws: string, ref: string) =>
  api.post(w(ws, "/review-queue/undo"), { ref });
export const unlearnReview = (ws: string, ref: string) =>
  api.post(w(ws, "/review-queue/unlearn"), { ref });
export const confirmAllSuggested = (ws: string) =>
  api.post<Json>(w(ws, "/review-queue/confirm-all-suggested"), {});
