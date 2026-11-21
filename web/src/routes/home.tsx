import { createRoute, redirect } from "@tanstack/react-router";
import { workspacesQuery } from "../api/queries";
import { requireUser } from "../guards";
import { startWorkspace } from "../last-workspace";
import { rootRoute } from "./root";

/** `/` is no page (spec S 4.4): it opens Mijn post of the workspace used last in this
 * browser (while still a member), else of the first; without any, the overview. */
export const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: async ({ context }) => {
    await requireUser(context.queryClient);
    const list = await context.queryClient.fetchQuery({ ...workspacesQuery, staleTime: 0 });
    const start = startWorkspace(list);
    if (!start) throw redirect({ to: "/werkruimtes", replace: true });
    throw redirect({
      to: "/w/$workspaceId/mijn-post",
      params: { workspaceId: start.id },
      replace: true,
    });
  },
});
