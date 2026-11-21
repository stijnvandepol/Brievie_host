/**
 * "Je kunt deze brief niet meer openen." after a change that took the letter out of the
 * caller's sight (the server answered `can_read: false`). Kept in memory only: the page the
 * caller lands on (Inbox or Alle post) reads it once and clears it.
 */
let pending: string | null = null;

export function showLetterGone(workspaceId: string) {
  pending = workspaceId;
}

/** Whether the page of this workspace should say it; clears it. */
export function takeLetterGone(workspaceId: string): boolean {
  const shown = pending === workspaceId;
  if (shown) pending = null;
  return shown;
}
