import { api, w } from "./http";
import type { JiraConnection, JiraTestResult } from "./types";

export const saveJiraConnection = (ws: string, body: unknown) =>
  api.put<JiraConnection>(w(ws, "/integrations/jira"), body);
export const testJira = (ws: string, project?: string) =>
  api.post<JiraTestResult>(w(ws, `/integrations/jira:test${project ? `?project=${project}` : ""}`));
export const rotateJiraSecret = (ws: string) =>
  api.post<JiraConnection>(w(ws, "/integrations/jira:rotate-secret"));
export const saveProjectJira = (ws: string, project: string, body: unknown) =>
  api.put<JiraConnection>(w(ws, `/projects/${project}/jira`), body);
