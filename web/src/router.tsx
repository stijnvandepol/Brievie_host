import type { QueryClient } from "@tanstack/react-query";
import { createRouter, type RouterHistory } from "@tanstack/react-router";
import { aiRoute } from "./routes/ai";
import { auditLogRoute } from "./routes/audit-log";
import { automationsRoute } from "./routes/automations";
import { documentRoute } from "./routes/document";
import { duplicateRoute } from "./routes/duplicate";
import { homeRoute } from "./routes/home";
import { inboxRoute } from "./routes/inbox";
import { invitationRoute } from "./routes/invitation";
import { labelsRoute } from "./routes/labels";
import { languageRoute } from "./routes/language";
import { loginRoute } from "./routes/login";
import { memberDevicesRoute } from "./routes/member-devices";
import { membersRoute } from "./routes/members";
import { pairAppRoute } from "./routes/pair-app";
import { passwordRoute } from "./routes/password";
import { passwordResetRoute } from "./routes/password-reset";
import { personsRoute } from "./routes/persons";
import { postRoute } from "./routes/post";
import { rootRoute } from "./routes/root";
import { newRuleRoute, ruleRoute } from "./routes/rule-editor";
import { searchRoute } from "./routes/search";
import { serverAuditLogRoute } from "./routes/server-audit-log";
import { sessionsRoute } from "./routes/sessions";
import { settingsRoute } from "./routes/settings";
import { setupRoute } from "./routes/setup";
import { tasksRoute } from "./routes/tasks";
import { trashRoute } from "./routes/trash";
import { visibilityRoute } from "./routes/visibility";
import { workspaceRoute } from "./routes/workspace";

const routeTree = rootRoute.addChildren([
  homeRoute,
  setupRoute,
  loginRoute,
  pairAppRoute,
  invitationRoute,
  passwordResetRoute,
  sessionsRoute,
  serverAuditLogRoute,
  passwordRoute,
  languageRoute,
  workspaceRoute.addChildren([
    tasksRoute,
    inboxRoute,
    postRoute,
    trashRoute,
    searchRoute,
    documentRoute,
    duplicateRoute,
    personsRoute,
    membersRoute,
    memberDevicesRoute,
    settingsRoute,
    auditLogRoute,
    visibilityRoute,
    labelsRoute,
    automationsRoute,
    aiRoute,
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
