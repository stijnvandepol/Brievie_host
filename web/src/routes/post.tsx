import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { documentsQuery, personsQuery } from "../api/queries";
import { DocumentTable } from "../components/document-table";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

function PostPage() {
  usePageTitle(t`Post`);
  const { workspace } = postRoute.useRouteContext();
  const { persoon } = postRoute.useSearch();
  const navigate = useNavigate();
  const persons = useQuery(personsQuery(workspace.id));
  const { data, error, isPending } = useQuery(documentsQuery(workspace.id, { person_id: persoon }));

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{t`Post`}</h1>
      </header>
      <div className="filter-bar">
        <label htmlFor="filter-person">{t`Persoon`}</label>
        <select
          id="filter-person"
          value={persoon ?? ""}
          onChange={(event) =>
            void navigate({
              to: "/w/$workspaceId/post",
              params: { workspaceId: workspace.id },
              search: event.target.value ? { persoon: event.target.value } : {},
            })
          }
        >
          <option value="">{t`Iedereen`}</option>
          {(persons.data ?? []).map((person) => (
            <option key={person.id} value={person.id}>
              {person.display_name}
            </option>
          ))}
        </select>
      </div>
      {isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={error ?? persons.error} />
      {data ? (
        <DocumentTable workspaceId={workspace.id} documents={data} persons={persons.data ?? []} />
      ) : null}
    </Shell>
  );
}

export const postRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "post",
  validateSearch: (search: Record<string, unknown>): { persoon?: string } =>
    typeof search.persoon === "string" && search.persoon ? { persoon: search.persoon } : {},
  component: PostPage,
});
