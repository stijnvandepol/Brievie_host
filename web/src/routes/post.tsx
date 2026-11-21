import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { documentsQuery, labelsQuery, personsQuery } from "../api/queries";
import { AiStatusLine } from "../components/ai-status-line";
import { DocumentTable } from "../components/document-table";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading } from "../components/ui";
import { workspaceRoute } from "./workspace";

type FilterOption = { value: string; text: string };

/** The current value as an extra option when the list does not hold it (yet or any more). */
function withCurrent(options: FilterOption[], value: string | undefined): FilterOption[] {
  return !value || options.some((option) => option.value === value)
    ? options
    : [...options, { value, text: t`(verwijderd)` }];
}

function PostPage() {
  usePageTitle(t`Alle post`);
  const { workspace } = postRoute.useRouteContext();
  const { persoon, label, automatisch } = postRoute.useSearch();
  const navigate = useNavigate();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const filtered = Boolean(persoon || label || automatisch);
  const { data, error, isPending } = useQuery(
    documentsQuery(workspace.id, {
      person_id: persoon,
      label_id: label,
      automatic: automatisch,
    }),
  );

  // A value in the URL that the list no longer holds stays visible, so the filter is not a lie.
  const personOptions = withCurrent(
    (persons.data ?? []).map((person) => ({ value: person.id, text: person.display_name })),
    persons.data ? persoon : undefined,
  );
  const labelOptions = withCurrent(
    (labels.data ?? []).map((option) => ({ value: option.id, text: option.name })),
    labels.data ? label : undefined,
  );

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Alle post`}
        description={t`Al je brieven, ook de afgehandelde. Zoek op persoon of label. Wat nog op jou wacht, staat in de Inbox.`}
      />
      <AiStatusLine workspaceId={workspace.id} />
      <div className="filter-bar">
        <div className="filter">
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
                  ...(automatisch ? { automatisch } : {}),
                },
              })
            }
          >
            <option value="">{t`Iedereen`}</option>
            {personOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.text}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
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
                  ...(automatisch ? { automatisch } : {}),
                },
              })
            }
          >
            <option value="">{t`Alle labels`}</option>
            {labelOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.text}
              </option>
            ))}
          </select>
        </div>
        <div className="check filter-check">
          <input
            id="filter-automatic"
            type="checkbox"
            checked={automatisch === true}
            onChange={(event) =>
              void navigate({
                to: "/w/$workspaceId/post",
                params: { workspaceId: workspace.id },
                search: {
                  ...(persoon ? { persoon } : {}),
                  ...(label ? { label } : {}),
                  ...(event.target.checked ? { automatisch: true } : {}),
                },
              })
            }
          />
          <label htmlFor="filter-automatic">{t`Automatisch door Brievie`}</label>
        </div>
      </div>
      {isPending ? <Loading /> : null}
      <ErrorAlert error={error ?? persons.error ?? labels.error} />
      {data ? (
        <DocumentTable
          workspaceId={workspace.id}
          documents={data}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
          empty={filtered ? t`Geen brieven met deze filters.` : t`Er is nog geen post.`}
          hint={
            filtered
              ? t`Kies bij Persoon of Label een andere waarde.`
              : t`Scan een brief met de Brievie-app op je telefoon. Daarna staat hij hier.`
          }
        />
      ) : null}
    </Shell>
  );
}

export const postRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  // The path stays "post" (bookmarks); the page is called "Alle post".
  path: "post",
  validateSearch: (
    search: Record<string, unknown>,
  ): { persoon?: string; label?: string; automatisch?: true } => ({
    ...(typeof search.persoon === "string" && search.persoon ? { persoon: search.persoon } : {}),
    ...(typeof search.label === "string" && search.label ? { label: search.label } : {}),
    ...(search.automatisch === true || search.automatisch === "true"
      ? { automatisch: true as const }
      : {}),
  }),
  component: PostPage,
});
