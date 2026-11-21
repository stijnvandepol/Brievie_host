/**
 * "Goed. We vragen het niet meer voor deze twee brieven." on "Te doen", after a person said
 * two letters are two letters. Kept in memory only: "Te doen" reads it when it opens and
 * then clears it, so it is shown once.
 */
let pending: string | null = null;

/** The next "Te doen" of this workspace shows the note. */
export function showDismissed(workspaceId: string) {
  pending = workspaceId;
}

/** Whether "Te doen" of this workspace should show the note (it stays until `clearDismissed`). */
export function isDismissedPending(workspaceId: string): boolean {
  return pending === workspaceId;
}

export function clearDismissed() {
  pending = null;
}
