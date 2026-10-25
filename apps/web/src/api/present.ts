import { api, w } from "./http";
import type { Json, Presentation, PresentationTemplate } from "./types";

export const createPresentation = (ws: string, body: { title: string; slides: unknown[] }) =>
  api.post<Presentation>(w(ws, "/presentations"), body);
export const addSlide = (ws: string, id: string, slide: unknown) =>
  api.post(w(ws, `/presentations/${id}/slides`), { slide });
export const fromTemplate = (ws: string, body: Json) =>
  api.post<Presentation>(w(ws, "/presentations:from-template"), body);
export const authorPresentation = (ws: string, brief: string) =>
  api.post<Presentation>(w(ws, "/presentations:author"), { brief });
export const importPresentation = (ws: string, deck: unknown) =>
  api.post<Presentation>(w(ws, "/presentations:import"), { deck, lenient: true });
export const deletePresentation = (ws: string, id: string) =>
  api.del(w(ws, `/presentations/${id}`));
export const restorePresentation = (ws: string, id: string) =>
  api.post(w(ws, `/presentations/${id}:restore`), {});
export const sharePresentation = (ws: string, id: string, enabled: boolean) =>
  api.post<{ url?: string }>(w(ws, `/presentations/${id}/share`), { enabled });

export const exportTemplate = (ws: string, key: string) =>
  api.get<Json>(w(ws, `/presentation-templates/${key}:export`));
export const validateTemplate = (ws: string, body: unknown) =>
  api.post<Json>(w(ws, "/presentation-templates:validate"), { body });
export const saveTemplate = (ws: string, id: string | undefined, body: unknown) =>
  id
    ? api.put<PresentationTemplate>(w(ws, `/presentation-templates/${id}`), { body })
    : api.post<PresentationTemplate>(w(ws, "/presentation-templates"), { body });
