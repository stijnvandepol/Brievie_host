import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, expect } from "vitest";
import { resetSearch } from "./search-term";
import { settleApps } from "./test-apps";
import { server } from "./test-server";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(async () => {
  const leftover = await settleApps();
  if (process.env.DEBUG_SETTLE && leftover.length > 0) {
    process.stderr.write(
      `UNSETTLED ${expect.getState().currentTestName}: ${leftover.join("; ")}\n`,
    );
  }
  cleanup();
  resetSearch();
  server.resetHandlers();
  document.cookie = "csrftoken=; Max-Age=0; path=/";
});
afterAll(() => server.close());

// A full suite runs many jsdom workers at once: the default 1 s for findBy/waitFor is too tight.
configure({ asyncUtilTimeout: 5000 });

// The router scrolls to the top after navigating; jsdom does not implement it.
window.scrollTo = () => {};
