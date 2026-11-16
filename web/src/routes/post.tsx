import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { documentsQuery, labelsQuery, personsQuery } from "../api/queries";
import { DocumentTable } from "../components/document-table";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

function PostPage() {
  usePageTitle(t`Post`);
  const { workspace } = postRoute.useRouteContext();
  const { persoon, label } = postRoute.useSearch();
  const navigate = useNavigate();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const { data, error, isPending } = useQuery(
    documentsQuery(workspace.id, { person_id: persoon, label_id: label }),
  );

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
              search: {
                ...(event.target.value ? { persoon: event.target.value } : {}),
                ...(label ? { label } : {}),
              },
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
        <label htmlFor="filter-label">{t`Label`}</label>
        <select
          id="filter-label"
          value={label ?? ""}
          onChange={(event) =>
            void navigate({
              to: "/w/$workspaceId/post",
              params: { workspaceId: workspace.id },
              search: {
                ...(persoon ? { persoon } : {}),
                ...(event.target.value ? { label: event.target.value } : {}),
              },
            })
          }
        >
          <option value="">{t`Alle labels`}</option>
          {(labels.data ?? []).map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
            </option>
          ))}
        </select>
      </div>
      {isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={error ?? persons.error ?? labels.error} />
      {data ? (
        <DocumentTable
          workspaceId={workspace.id}
          documents={data}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
        />
      ) : null}
    </Shell>
  );
}

export const postRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "post",
  validateSearch: (search: Record<string, unknown>): { persoon?: string; label?: string } => ({
    ...(typeof search.persoon === "string" && search.persoon ? { persoon: search.persoon } : {}),
    ...(typeof search.label === "string" && search.label ? { label: search.label } : {}),
  }),
  component: PostPage,
});
