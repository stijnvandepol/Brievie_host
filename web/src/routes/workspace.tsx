import { useQuery } from "@tanstack/react-query";
import { createRoute, notFound, Outlet } from "@tanstack/react-router";
import { workspacesQuery } from "../api/queries";
import { PageTitleSuffix } from "../components/page";
import { requireUser } from "../guards";
import { rememberWorkspace } from "../last-workspace";
import { searchInWorkspace } from "../search-term";
import { undoInWorkspace } from "../undo-trash";
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
    undoInWorkspace(workspace.id);
    rememberWorkspace(workspace.id);
    return { workspace };
  },
  component: WorkspaceOutlet,
});

/** The pages of one workspace, drawn afresh for another: a switch from `/w/A/inbox` to
 * `/w/B/inbox` mounts the page again, so nothing a page keeps of A (state read once when
 * it opened) shows in B (spec S 4.3; the old switch looked dead, S 2.1). */
function WorkspaceOutlet() {
  const { workspace } = workspaceRoute.useRouteContext();
  // With several workspaces the tab's title names this one, so two tabs are told apart.
  const { data: list } = useQuery(workspacesQuery);
  const suffix = list && list.length > 1 ? workspace.name : "";
  return (
    <PageTitleSuffix value={suffix}>
      <Outlet key={workspace.id} />
    </PageTitleSuffix>
  );
}
