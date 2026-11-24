/** Hands `blob` to the browser as a download named `name` (no page change, no server). */
export function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  // After the click: the browser has started the download by then.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
