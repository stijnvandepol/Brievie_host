import { t } from "@lingui/core/macro";
import { createRoute } from "@tanstack/react-router";
import { PageHeader, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

/** "Mijn taken": what the member has to do with their post, by date. */
function TasksPage() {
  usePageTitle(t`Mijn taken`);
  const { workspace } = tasksRoute.useRouteContext();
  return (
    <Shell workspace={workspace}>
      <PageHeader title={t`Mijn taken`} />
    </Shell>
  );
}

export const tasksRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "taken",
  component: TasksPage,
});
