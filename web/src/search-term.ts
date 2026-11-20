import { useSyncExternalStore } from "react";

/**
 * The term the user searches for, kept in memory only: never in the URL, the history state,
 * storage or a query key (it can be a name or a number). A reload forgets it on purpose.
 * `run` counts searches, so searching the same term again still runs.
 */
type SearchRequest = { term: string; run: number };

let current: SearchRequest = { term: "", run: 0 };
// The workspace the term was typed in: another workspace starts without it.
let owner: string | null = null;
const listeners = new Set<() => void>();

export function requestSearch(term: string) {
  current = { term, run: current.run + 1 };
  for (const listener of listeners) listener();
}

/** Forgets the term (and so the results): on signing out and on a workspace switch. */
export function resetSearch() {
  owner = null;
  if (current.term === "" && current.run === 0) return;
  current = { term: "", run: 0 };
  for (const listener of listeners) listener();
}

/** Called on entering a workspace: the term of another workspace is forgotten. */
export function searchInWorkspace(workspaceId: string) {
  if (owner !== null && owner !== workspaceId) resetSearch();
  owner = workspaceId;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useSearchRequest(): SearchRequest {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => current,
  );
}
