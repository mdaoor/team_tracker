import { api, w } from "./http";

export const approveBaseline = (ws: string, project: string, name: string) =>
  api.post(w(ws, `/projects/${project}/baselines?name=${encodeURIComponent(name)}`));
