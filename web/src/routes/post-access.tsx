import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { ApiFailure, api } from "../api/client";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading } from "../components/ui";
import { workspaceRoute } from "./workspace";

/** "Wie kan mijn post zien?": the server's sentences for the caller, read-only, for every
 * member (S 4.15). */
function PostAccessPage() {
  usePageTitle(t`Wie kan mijn post zien?`);
  const { workspace } = postAccessRoute.useRouteContext();
  const access = useQuery({
    queryKey: ["workspaces", workspace.id, "post-access"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/post-access", {
        params: { path: { workspace_id: workspace.id } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Wie kan mijn post zien?`}
        description={t`Zo is het nu in ${workspace.name}. Alleen een beheerder kan dit veranderen.`}
      />
      {access.isPending ? <Loading /> : null}
      <ErrorAlert error={access.error} />
      {access.data ? (
        <section className="panel panel-pad" aria-label={t`Wie kan mijn post zien?`}>
          <ul className="sentence-list">
            {access.data.sentences.map((sentence) => (
              <li key={sentence}>{sentence}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </Shell>
  );
}

export const postAccessRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "wie-ziet-mijn-post",
  component: PostAccessPage,
});
