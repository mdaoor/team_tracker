import { api, w } from "./http";
import type { AuthProviders, Json, Me, Workspace } from "./types";

export const getMe = (ws: string) => api.get<Me>(w(ws, "/me"));
export const listMyWorkspaces = () => api.get<{ data?: Workspace[] }>("/api/v1/me/workspaces");
export const getAuthProviders = () => api.get<AuthProviders>("/auth/providers");
export const devLogin = (body: { workspace: string; handle: string }) =>
  api.post<Json>("/auth/dev/login", body);
export const ldapLogin = (body: { workspace: string; username: string; password: string }) =>
  api.post<Json>("/auth/ldap/login", body);
export const enableExternalShare = (ws: string) =>
  api.patch<Json>(w(ws, "/settings"), { external_share: true });
