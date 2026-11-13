import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./test-server";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  document.cookie = "csrftoken=; Max-Age=0; path=/";
});
afterAll(() => server.close());

// The router scrolls to the top after navigating; jsdom does not implement it.
window.scrollTo = () => {};
