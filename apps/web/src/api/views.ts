import { api, w } from "./http";

export const createView = (ws: string, body: unknown) => api.post(w(ws, "/views"), body);
