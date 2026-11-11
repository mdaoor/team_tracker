import { api } from "../api/http";

/** Hand a blob to the browser as a file download. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

type Toasts = {
  push: (text: string, o?: { key?: string; ms?: number }) => void;
  error: (e: unknown) => void;
};

/**
 * POST for a file and download it, with a pending toast and a success toast naming the file.
 * Errors go to the error toast (with the request id); the function never throws.
 */
export async function downloadPost(
  toasts: Toasts,
  path: string,
  body: unknown,
  filename: string,
  label = "file",
): Promise<boolean> {
  toasts.push(`Preparing ${label}…`, { key: `dl:${filename}`, ms: 2500 });
  try {
    const blob = await api.postBlob(path, body);
    saveBlob(blob, filename);
    toasts.push(`Downloaded ${filename}`, { key: `dl-ok:${filename}` });
    return true;
  } catch (e) {
    toasts.error(e);
    return false;
  }
}
