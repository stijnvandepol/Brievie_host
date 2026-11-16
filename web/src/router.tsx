import type { QueryClient } from "@tanstack/react-query";
import { createRouter, type RouterHistory } from "@tanstack/react-router";
import { automationsRoute } from "./routes/automations";
import { documentRoute } from "./routes/document";
import { homeRoute } from "./routes/home";
import { inboxRoute } from "./routes/inbox";
import { labelsRoute } from "./routes/labels";
import { loginRoute } from "./routes/login";
import { pairAppRoute } from "./routes/pair-app";
import { personsRoute } from "./routes/persons";
import { postRoute } from "./routes/post";
import { rootRoute } from "./routes/root";
import { newRuleRoute, ruleRoute } from "./routes/rule-editor";
import { setupRoute } from "./routes/setup";
import { workspaceRoute } from "./routes/workspace";

const routeTree = rootRoute.addChildren([
  homeRoute,
  setupRoute,
  loginRoute,
  pairAppRoute,
  workspaceRoute.addChildren([
    inboxRoute,
    postRoute,
    documentRoute,
    personsRoute,
    labelsRoute,
    automationsRoute,
    newRuleRoute,
    ruleRoute,
  ]),
]);

export function createAppRouter(queryClient: QueryClient, history?: RouterHistory) {
  return createRouter({ routeTree, history, context: { queryClient } });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
