import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { getMe } from "../api/session";
import { api, w, type Json } from "../api/http";
import type { Me } from "../api/types";

export const useWs = (): string => useParams().ws ?? "";

/** Per-workspace cache keys: switching workspace never shows another workspace's data. */
export function useList<T extends Json = Json>(path: string, query?: Json, enabled = true) {
  const ws = useWs();
  return useQuery({
    queryKey: [ws, path, query ?? null],
    queryFn: async () => ((await api.get<{ data?: T[] }>(w(ws, path), query)).data ?? []) as T[],
    enabled: enabled && !!ws,
  });
}

export function useObj<T extends Json = Json>(path: string, query?: Json, enabled = true) {
  const ws = useWs();
  return useQuery({
    queryKey: [ws, path, query ?? null],
    queryFn: () => api.get<T>(w(ws, path), query),
    enabled: enabled && !!ws,
  });
}

/** The signed-in person and what they may do (drives which governance controls render). */
export function useMe() {
  const ws = useWs();
  const q = useQuery({
    queryKey: [ws, "/me"],
    queryFn: () => getMe(ws),
    enabled: !!ws,
  });
  const can = (q.data?.can ?? {}) as Record<string, boolean>;
  return { me: q.data as Me | undefined, can, admin: !!q.data?.is_admin, loading: q.isLoading };
}
