import { useSyncExternalStore } from "react";

/**
 * The term the user searches for, kept in memory only: never in the URL, the history state,
 * storage or a query key (it can be a name or a number). A reload forgets it on purpose.
 * `run` counts searches, so searching the same term again still runs.
 */
type SearchRequest = { term: string; run: number };

let current: SearchRequest = { term: "", run: 0 };
const listeners = new Set<() => void>();

export function requestSearch(term: string) {
  current = { term, run: current.run + 1 };
  for (const listener of listeners) listener();
}

export function resetSearch() {
  current = { term: "", run: 0 };
  for (const listener of listeners) listener();
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
