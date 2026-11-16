import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { documentsQuery } from "../api/queries";
import { DocumentTable } from "../components/document-table";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

function PostPage() {
  usePageTitle(t`Post`);
  const { workspace } = postRoute.useRouteContext();
  const { data, error, isPending } = useQuery(documentsQuery(workspace.id));

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{t`Post`}</h1>
      </header>
      {isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={error} />
      {data ? <DocumentTable workspaceId={workspace.id} documents={data} /> : null}
    </Shell>
  );
}

export const postRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "post",
  component: PostPage,
});
