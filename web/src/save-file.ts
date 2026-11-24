const REVOKE_AFTER_MS = 10_000;

/** Hands `blob` to the browser as a download named `name` (no page change, no server). */
export function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  // Well after the click: Safari may still be reading the file a moment later, and a
  // download that lost its file fails without a word.
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}
