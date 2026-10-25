import { api, w } from "./http";
import type { Json } from "./types";

export const saveJiraConnection = (ws: string, body: unknown) =>
  api.put<Json>(w(ws, "/integrations/jira"), body);
export const testJira = (ws: string, project?: string) =>
  api.post<Json>(w(ws, `/integrations/jira:test${project ? `?project=${project}` : ""}`));
export const rotateJiraSecret = (ws: string) =>
  api.post<Json>(w(ws, "/integrations/jira:rotate-secret"));
export const saveProjectJira = (ws: string, project: string, body: unknown) =>
  api.put<Json>(w(ws, `/projects/${project}/jira`), body);
