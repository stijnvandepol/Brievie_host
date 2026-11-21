import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { documentsQuery, labelsQuery, personsQuery } from "../api/queries";
import { DocumentTable } from "../components/document-table";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading } from "../components/ui";
import { workspaceRoute } from "./workspace";

/** "Mijn post" (spec S 4.4, B1): the letters on the signed-in person's own name, the start
 * page of a workspace. */
function MyPostPage() {
  usePageTitle(t`Mijn post`);
  const { workspace } = myPostRoute.useRouteContext();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const { data, error, isPending } = useQuery(
    documentsQuery(workspace.id, { addressed_to_me: true }),
  );
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Mijn post`}
        description={t`De brieven op jouw naam. Wat nog nagekeken of verdeeld moet worden, staat in de Inbox.`}
      />
      {isPending ? <Loading /> : null}
      <ErrorAlert error={error ?? persons.error ?? labels.error} />
      {data ? (
        <DocumentTable
          workspaceId={workspace.id}
          documents={data}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
          empty={t`Er is nog geen post op jouw naam.`}
          hint={t`Post op jouw naam staat hier zodra hij is gescand en verdeeld.`}
        />
      ) : null}
    </Shell>
  );
}

export const myPostRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "mijn-post",
  component: MyPostPage,
});
