import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { ToggleButton, ToggleButtonGroup } from "react-aria-components";
import { documentsQuery, labelsQuery, letterStatusCountsQuery, personsQuery } from "../api/queries";
import { AiStatusLine } from "../components/ai-status-line";
import { DocumentTable } from "../components/document-table";
import {
  apiStatus,
  letterCountsText,
  STATUS_FILTERS,
  type StatusFilter,
} from "../components/letter-status";
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

type PostSearch = {
  persoon?: string;
  label?: string;
  automatisch?: true;
  ongelezen?: true;
  status?: StatusFilter;
  telaat?: true;
};

function PostPage() {
  usePageTitle(t`Alle post`);
  const { workspace } = postRoute.useRouteContext();
  const search = postRoute.useSearch();
  const { persoon, label, automatisch, ongelezen, status, telaat } = search;
  const navigate = useNavigate();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const counts = useQuery(letterStatusCountsQuery(workspace.id, persoon));
  const filtered = Boolean(persoon || label || automatisch || ongelezen || status || telaat);
  const { data, error, isPending } = useQuery(
    documentsQuery(workspace.id, {
      person_id: persoon,
      label_id: label,
      automatic: automatisch,
      unread_only: ongelezen,
      letter_status: apiStatus(status),
      overdue: telaat,
    }),
  );

  /** The same page with other filters; a filter set to `undefined` is taken away. */
  function go(change: { [K in keyof PostSearch]?: PostSearch[K] | undefined }) {
    const next: PostSearch = { ...search, ...change };
    const clean = Object.fromEntries(
      Object.entries(next).filter(([, value]) => value !== undefined),
    ) as PostSearch;
    void navigate({
      to: "/w/$workspaceId/post",
      params: { workspaceId: workspace.id },
      search: clean,
    });
  }

  // A value in the URL that the list no longer holds stays visible, so the filter is not a lie.
  const personOptions = withCurrent(
    (persons.data ?? []).map((person) => ({ value: person.id, text: person.display_name })),
    persons.data ? persoon : undefined,
  );
  const labelOptions = withCurrent(
    (labels.data ?? []).map((option) => ({ value: option.id, text: option.name })),
    labels.data ? label : undefined,
  );
  const personName = persons.data?.find((person) => person.id === persoon)?.display_name;
  const summary = counts.data ? letterCountsText(counts.data) : "";
  const fresh = counts.data?.new;
  const open = counts.data?.open;
  const waiting = counts.data?.waiting;
  const handled = counts.data?.handled;
  const late = counts.data?.overdue;

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={personName ? t`Post van ${personName}` : t`Alle post`}
        description={t`Al je brieven, ook de afgehandelde. Zoek op persoon of label. Wat nog op jou wacht, staat in de Inbox.`}
      />
      <AiStatusLine workspaceId={workspace.id} />
      {summary ? (
        <p className="letter-counts" aria-live="polite">
          {persoon ? summary : t`Je post: ${summary}`}
        </p>
      ) : null}
      <ToggleButtonGroup
        className="segmented"
        aria-label={t`Status van de brief`}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[status ?? "alles"]}
        onSelectionChange={(keys) => {
          const [next] = [...keys];
          go({ status: STATUS_FILTERS.find((entry) => entry.url === next)?.url });
        }}
      >
        <ToggleButton id="alles" className="segment">
          {t`Alles`}
        </ToggleButton>
        <ToggleButton id="nieuw" className="segment">
          {fresh === undefined ? t`Nieuw` : t`Nieuw (${fresh})`}
        </ToggleButton>
        <ToggleButton id="openstaand" className="segment">
          {open === undefined ? t`Openstaand` : t`Openstaand (${open})`}
        </ToggleButton>
        <ToggleButton id="wacht" className="segment">
          {waiting === undefined ? t`Wacht op reactie` : t`Wacht op reactie (${waiting})`}
        </ToggleButton>
        <ToggleButton id="afgehandeld" className="segment">
          {handled === undefined ? t`Afgehandeld` : t`Afgehandeld (${handled})`}
        </ToggleButton>
      </ToggleButtonGroup>
      <div className="filter-bar">
        <div className="filter">
          <label htmlFor="filter-person">{t`Persoon`}</label>
          <select
            id="filter-person"
            value={persoon ?? ""}
            onChange={(event) => go({ persoon: event.target.value || undefined })}
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
            onChange={(event) => go({ label: event.target.value || undefined })}
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
            id="filter-overdue"
            type="checkbox"
            checked={telaat === true}
            onChange={(event) => go({ telaat: event.target.checked ? true : undefined })}
          />
          <label htmlFor="filter-overdue">
            {late === undefined ? t`Alleen te laat` : t`Alleen te laat (${late})`}
          </label>
        </div>
        <div className="check filter-check">
          <input
            id="filter-automatic"
            type="checkbox"
            checked={automatisch === true}
            onChange={(event) => go({ automatisch: event.target.checked ? true : undefined })}
          />
          <label htmlFor="filter-automatic">{t`Automatisch door Brievie`}</label>
        </div>
        <div className="check filter-check">
          <input
            id="filter-unread"
            type="checkbox"
            checked={ongelezen === true}
            onChange={(event) => go({ ongelezen: event.target.checked ? true : undefined })}
          />
          <label htmlFor="filter-unread">{t`Alleen ongelezen`}</label>
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
          showUnread
          letterStatus="all"
          empty={
            status || telaat
              ? t`Geen brieven met deze status.`
              : filtered
                ? t`Geen brieven met deze filters.`
                : t`Er is nog geen post.`
          }
          hint={
            filtered
              ? t`Kies bij Persoon, Label of Status een andere waarde.`
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
  validateSearch: (search: Record<string, unknown>): PostSearch => ({
    ...(typeof search.persoon === "string" && search.persoon ? { persoon: search.persoon } : {}),
    ...(typeof search.label === "string" && search.label ? { label: search.label } : {}),
    ...(search.automatisch === true || search.automatisch === "true"
      ? { automatisch: true as const }
      : {}),
    ...(search.ongelezen === true || search.ongelezen === "true"
      ? { ongelezen: true as const }
      : {}),
    ...(STATUS_FILTERS.some((entry) => entry.url === search.status)
      ? { status: search.status as StatusFilter }
      : {}),
    ...(search.telaat === true || search.telaat === "true" ? { telaat: true as const } : {}),
  }),
  component: PostPage,
});
