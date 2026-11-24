import type { QueryClient } from "@tanstack/react-query";
import { createRouter, type RouterHistory } from "@tanstack/react-router";
import { aiRoute } from "./routes/ai";
import { auditLogRoute } from "./routes/audit-log";
import { automationsRoute } from "./routes/automations";
import { backupRoute } from "./routes/backup";
import { departmentsRoute } from "./routes/departments";
import { documentRoute } from "./routes/document";
import { duplicateRoute } from "./routes/duplicate";
import { homeRoute } from "./routes/home";
import { inboxRoute } from "./routes/inbox";
import { invitationRoute } from "./routes/invitation";
import { labelsRoute } from "./routes/labels";
import { languageRoute } from "./routes/language";
import { loginRoute } from "./routes/login";
import { memberDevicesRoute } from "./routes/member-devices";
import { myPostRoute } from "./routes/my-post";
import { pairAppRoute } from "./routes/pair-app";
import { passwordRoute } from "./routes/password";
import { passwordResetRoute } from "./routes/password-reset";
import { membersRedirectRoute, peopleRoute, personsRedirectRoute } from "./routes/people";
import { personDetailRoute } from "./routes/people-detail";
import { postRoute } from "./routes/post";
import { postAccessRoute } from "./routes/post-access";
import { recoverySheetRoute } from "./routes/recovery-sheet";
import { rootRoute } from "./routes/root";
import { newRuleRoute, ruleRoute } from "./routes/rule-editor";
import { searchRoute } from "./routes/search";
import { securityRoute } from "./routes/security";
import { serverAuditLogRoute } from "./routes/server-audit-log";
import { sessionsRoute } from "./routes/sessions";
import { settingsRoute } from "./routes/settings";
import { setupRoute } from "./routes/setup";
import { setupSummaryRoute } from "./routes/setup-summary";
import { tasksRoute } from "./routes/tasks";
import { trashRoute } from "./routes/trash";
import { visibilityRoute } from "./routes/visibility";
import { workspaceRoute } from "./routes/workspace";
import { workspacesRoute } from "./routes/workspaces";

const routeTree = rootRoute.addChildren([
  homeRoute,
  workspacesRoute,
  setupRoute,
  loginRoute,
  pairAppRoute,
  invitationRoute,
  passwordResetRoute,
  sessionsRoute,
  serverAuditLogRoute,
  recoverySheetRoute,
  backupRoute,
  passwordRoute,
  securityRoute,
  languageRoute,
  workspaceRoute.addChildren([
    myPostRoute,
    tasksRoute,
    inboxRoute,
    postRoute,
    trashRoute,
    searchRoute,
    documentRoute,
    duplicateRoute,
    peopleRoute,
    personDetailRoute,
    personsRedirectRoute,
    membersRedirectRoute,
    departmentsRoute,
    memberDevicesRoute,
    settingsRoute,
    auditLogRoute,
    visibilityRoute,
    postAccessRoute,
    setupSummaryRoute,
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
