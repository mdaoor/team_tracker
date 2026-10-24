import { api, ApiError, type Row } from "../api/http";

export type Conflict = { current: Row; etag: string; attempted: Row };

/** PATCH with `If-Match`; a 409 becomes a `Conflict` the dialog can resolve (keep mine / take theirs / revert). */
export async function patchWithEtag(
  path: string,
  body: Row,
  etag: string,
): Promise<{ ok: true; data: Row } | { ok: false; conflict: Conflict }> {
  try {
    return { ok: true, data: await api.patch(path, body, { "If-Match": etag }) };
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const d = e.body?.error?.details ?? e.body?.error ?? {};
      return {
        ok: false,
        conflict: { current: d.current ?? {}, etag: d.etag ?? etag, attempted: body },
      };
    }
    throw e;
  }
}
