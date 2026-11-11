import { api, w } from "./http";
import type { DryRunResult, Json, Link } from "./types";

export const idpSync = (ws: string) => api.post<Json>(w(ws, "/idp/sync"), {});
export const deleteIdpMapping = (ws: string, id: string) => api.del(w(ws, `/idp/mappings/${id}`));
export const productLinks = (ws: string, productKey: string) =>
  api.get<{ data?: Link[] }>(w(ws, `/products/${productKey}/links`));
export const reparentProduct = (ws: string, key: string, parentId: string | null) =>
  api.patch(w(ws, `/products/${key}`), { parent_product_id: parentId });
/** Moving a project between products is unlink + link (`DELETE /products/{ref}/links/{project}`, then `POST .../links`). */
export const unlinkProject = (ws: string, productKey: string, projectKey: string) =>
  api.del(w(ws, `/products/${productKey}/links/${projectKey}`));
export const linkProject = (
  ws: string,
  productKey: string,
  projectKey: string,
  role: string,
  weight?: number | null,
) =>
  api.post(w(ws, `/products/${productKey}/links`), {
    target_type: "project",
    target_id: projectKey,
    role,
    ...(weight == null ? {} : { weight }),
  });
/** Re-parent an item inside its own project (`parent` null = top level); the server refuses another project's parent. */
export const moveItem = (ws: string, key: string, parent: string | null) =>
  api.post(w(ws, `/items/${key}/move`), { parent });
export const linkRisk = (ws: string, risk: string, item: string) =>
  api.post(w(ws, `/risks/${risk}/links`), { item });
export const bulkDimensionValues = (ws: string, key: string, values: unknown) =>
  api.post(w(ws, `/dimensions/${key}/values:bulk`), values);
export const markGlossaryManual = (ws: string, id: string) =>
  api.patch(w(ws, `/glossary/${id}`), { source: "manual" });
export const deleteGlossary = (ws: string, id: string) => api.del(w(ws, `/glossary/${id}`));
export const deleteRule = (ws: string, id: string) => api.del(w(ws, `/translator-rules/${id}`));
export const dryRunRule = (ws: string, body: unknown) =>
  api.post<DryRunResult>(w(ws, "/translator-rules:dry-run"), body);
export const getContextPack = (ws: string, who?: string) =>
  api.get(w(ws, "/context-pack"), who ? { for: who } : undefined);
export const reorderStages = (ws: string, process: string, order: string[]) =>
  api.patch(w(ws, `/processes/${process}/stages:reorder`), { order });
export const patchStage = (ws: string, process: string, stage: string, body: unknown) =>
  api.patch(w(ws, `/processes/${process}/stages/${stage}`), body);
