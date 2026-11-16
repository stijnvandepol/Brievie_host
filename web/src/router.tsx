import type { QueryClient } from "@tanstack/react-query";
import { createRouter, type RouterHistory } from "@tanstack/react-router";
import { documentRoute } from "./routes/document";
import { homeRoute } from "./routes/home";
import { loginRoute } from "./routes/login";
import { pairAppRoute } from "./routes/pair-app";
import { postRoute } from "./routes/post";
import { rootRoute } from "./routes/root";
import { setupRoute } from "./routes/setup";
import { workspaceRoute } from "./routes/workspace";

const routeTree = rootRoute.addChildren([
  homeRoute,
  setupRoute,
  loginRoute,
  pairAppRoute,
  workspaceRoute.addChildren([postRoute, documentRoute]),
]);

export function createAppRouter(queryClient: QueryClient, history?: RouterHistory) {
  return createRouter({ routeTree, history, context: { queryClient } });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
