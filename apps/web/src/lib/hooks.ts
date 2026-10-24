import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, w, type Row } from "../api/http";

export const useWs = (): string => useParams().ws ?? "";

/** Per-workspace cache keys: switching workspace never shows another workspace's data. */
export function useList(path: string, query?: Row, enabled = true) {
  const ws = useWs();
  return useQuery({
    queryKey: [ws, path, query ?? null],
    queryFn: async () => ((await api.get<Row>(w(ws, path), query)).data ?? []) as Row[],
    enabled: enabled && !!ws,
  });
}

export function useObj(path: string, query?: Row, enabled = true) {
  const ws = useWs();
  return useQuery({
    queryKey: [ws, path, query ?? null],
    queryFn: () => api.get<Row>(w(ws, path), query),
    enabled: enabled && !!ws,
  });
}
