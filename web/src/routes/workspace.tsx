import { createRoute, notFound, Outlet } from "@tanstack/react-router";
import { workspacesQuery } from "../api/queries";
import { requireUser } from "../guards";
import { searchInWorkspace } from "../search-term";
import { rootRoute } from "./root";

/** Everything below `/w/<id>`: the id must be one of the user's workspaces. */
export const workspaceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/w/$workspaceId",
  beforeLoad: async ({ context, params }) => {
    await requireUser(context.queryClient);
    const workspaces = await context.queryClient.fetchQuery({ ...workspacesQuery, staleTime: 0 });
    const workspace = workspaces.find((candidate) => candidate.id === params.workspaceId);
    if (!workspace) throw notFound();
    searchInWorkspace(workspace.id);
    return { workspace };
  },
  component: Outlet,
});
