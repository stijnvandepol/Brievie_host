/**
 * The workspace this browser used last, so `/` opens it again (spec S 4.3). A per-viewer
 * convenience in `localStorage`: blocked storage (a private window) only means `/` opens
 * the first workspace instead.
 */
const KEY = "brievie.last-workspace";

export function rememberWorkspace(id: string): void {
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    // Storage blocked: nothing to remember.
  }
}

export function lastWorkspace(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** Where `/` goes: the last workspace while the user is still a member, else the first. */
export function startWorkspace<T extends { id: string }>(list: T[]): T | undefined {
  const last = lastWorkspace();
  return list.find((workspace) => workspace.id === last) ?? list[0];
}
