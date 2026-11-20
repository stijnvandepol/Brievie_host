import { useSyncExternalStore } from "react";

/**
 * The letter that was just moved to the trash, for the undo bar ("Ongedaan maken"). Kept in
 * memory only. There is no timer (WCAG 2.2.1): the bar stays until it is closed, another
 * letter is trashed, the workspace changes or the user signs out.
 */
export type UndoTrash = { workspaceId: string; documentId: string };

let current: UndoTrash | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/** Offers to put this letter back; replaces the offer for an earlier one. */
export function showUndo(workspaceId: string, documentId: string) {
  current = { workspaceId, documentId };
  emit();
}

export function clearUndo() {
  if (current === null) return;
  current = null;
  emit();
}

/** Called on entering a workspace: the offer of another workspace is dropped. */
export function undoInWorkspace(workspaceId: string) {
  if (current !== null && current.workspaceId !== workspaceId) clearUndo();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useUndo(): UndoTrash | null {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => current,
  );
}
