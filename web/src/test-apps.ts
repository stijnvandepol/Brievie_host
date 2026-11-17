import type { QueryClient } from "@tanstack/react-query";
import type { createAppRouter } from "./router";

/*
 * The apps a test rendered with `renderApp`. Kept apart from `test-utils` so the setup file
 * can wait for them without importing the app (that would load modules before a test file's
 * `vi.mock` could replace them).
 */

export type App = { router: ReturnType<typeof createAppRouter>; queryClient: QueryClient };

/** The apps the current test rendered; `settleApps` waits for them after the test. */
const mounted: App[] = [];

export function trackApp(app: App) {
  mounted.push(app);
}

/** What an app is still doing: a route loading, a request or a mutation on its way. */
function busy({ router, queryClient }: App): string[] {
  const work: string[] = [];
  const state = router.state;
  if (state.status !== "idle" || state.isLoading) {
    work.push(`router ${state.status} at ${state.location.href}`);
  }
  if (state.resolvedLocation?.href !== state.location.href) {
    work.push(`navigation to ${state.location.href} not resolved`);
  }
  if (queryClient.isFetching() > 0) work.push(`${queryClient.isFetching()} queries fetching`);
  if (queryClient.isMutating() > 0) work.push(`${queryClient.isMutating()} mutations running`);
  return work;
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

/**
 * Waits until every app of the test is at rest, then forgets them. A test that ends while a
 * navigation or request is on its way leaves a React update behind, which lands in the next
 * test or after jsdom is gone ("window is not defined", seen in CI). Runs before the test's
 * apps are unmounted. Returns what was still running when the test ended; fails when the
 * apps do not come to rest within 5 seconds, so a leak is an error and never silent.
 */
export async function settleApps(): Promise<string[]> {
  const leftover = mounted.flatMap(busy);
  // Quiet twice in a row: a finished load can start the next one (a redirect, a refetch).
  let quiet = 0;
  for (let rounds = 0; quiet < 2 && rounds < 250; rounds += 1) {
    await tick();
    quiet = mounted.some((app) => busy(app).length > 0) ? 0 : quiet + 1;
  }
  const still = mounted.flatMap(busy);
  for (const app of mounted.splice(0)) app.queryClient.clear();
  if (quiet < 2)
    throw new Error(`The app did not come to rest after the test: ${still.join("; ")}`);
  return leftover;
}
