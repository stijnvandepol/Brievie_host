import { plural, t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { ToggleButton, ToggleButtonGroup } from "react-aria-components";
import {
  archiveFacetsQuery,
  documentsQuery,
  labelsQuery,
  letterStatusCountsQuery,
  personsQuery,
} from "../api/queries";
import { AiStatusLine } from "../components/ai-status-line";
import { DocumentTable, typeLabel } from "../components/document-table";
import { apiStatus, STATUS_FILTERS, type StatusFilter } from "../components/letter-status";
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

/** The statuses Archief filters on: a letter in the archive is checked, so never "Nieuw". */
const ARCHIVE_STATUSES = STATUS_FILTERS.filter((entry) => entry.url !== "nieuw");
type ArchiveStatus = Exclude<StatusFilter, "nieuw">;

const KINDS = { brieven: "letter", documenten: "document" } as const;
type KindFilter = keyof typeof KINDS;

export type ArchiveSearch = {
  soort?: KindFilter;
  type?: string;
  persoon?: string;
  afzender?: string;
  jaar?: number;
  label?: string;
  status?: ArchiveStatus;
  telaat?: true;
  automatisch?: true;
  "loopt-af"?: true;
};

function ArchivePage() {
  usePageTitle(t`Archief`);
  const { workspace } = archiveRoute.useRouteContext();
  const search = archiveRoute.useSearch();
  const { soort, type, persoon, afzender, jaar, label, status, telaat, automatisch } = search;
  const expiring = search["loopt-af"];
  const navigate = useNavigate();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const facets = useQuery(archiveFacetsQuery(workspace.id));
  const counts = useQuery(letterStatusCountsQuery(workspace.id, persoon));
  const filtered = Boolean(
    soort || type || persoon || afzender || jaar || label || status || telaat || automatisch,
  );
  const { data, error, isPending } = useQuery(
    documentsQuery(workspace.id, {
      archived: true,
      kind: soort ? KINDS[soort] : undefined,
      document_type: type,
      person_id: persoon,
      sender: afzender,
      year: jaar,
      label_id: label,
      automatic: automatisch,
      letter_status: apiStatus(status),
      overdue: telaat,
      expiring,
    }),
  );

  /** The same page with other filters; a filter set to `undefined` is taken away. */
  function go(change: { [K in keyof ArchiveSearch]?: ArchiveSearch[K] | undefined }) {
    const next: ArchiveSearch = { ...search, ...change };
    const clean = Object.fromEntries(
      Object.entries(next).filter(([, value]) => value !== undefined),
    ) as ArchiveSearch;
    void navigate({
      to: "/w/$workspaceId/archief",
      params: { workspaceId: workspace.id },
      search: clean,
    });
  }

  const counted = (text: string, count: number) => t`${text} (${count})`;
  // A value in the URL that the list no longer holds stays visible, so the filter is not a lie.
  const personOptions = withCurrent(
    (persons.data ?? []).map((person) => ({ value: person.id, text: person.display_name })),
    persons.data ? persoon : undefined,
  );
  const labelOptions = withCurrent(
    (labels.data ?? []).map((option) => ({ value: option.id, text: option.name })),
    labels.data ? label : undefined,
  );
  const typeOptions = withCurrent(
    (facets.data?.types ?? []).map((facet) => ({
      value: facet.value,
      text: counted(typeLabel(facet.value) || facet.value, facet.count),
    })),
    facets.data ? type : undefined,
  );
  const senderOptions = withCurrent(
    (facets.data?.senders ?? []).map((facet) => ({
      value: facet.value,
      text: counted(facet.value, facet.count),
    })),
    facets.data ? afzender : undefined,
  );
  const yearOptions = withCurrent(
    (facets.data?.years ?? []).map((facet) => ({
      value: facet.value,
      text: counted(facet.value, facet.count),
    })),
    facets.data && jaar ? String(jaar) : undefined,
  );
  const kindCount = (kind: string) =>
    facets.data?.kinds?.find((facet) => facet.value === kind)?.count;
  const total = (facets.data?.kinds ?? []).reduce((sum, facet) => sum + facet.count, 0);
  const letters = kindCount("letter");
  const documents = kindCount("document");
  const personName = persons.data?.find((person) => person.id === persoon)?.display_name;
  const open = counts.data?.open;
  const waiting = counts.data?.waiting;
  const handled = counts.data?.handled;
  const late = counts.data?.overdue;

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={personName ? t`Archief van ${personName}` : t`Archief`}
        description={t`Elke gecontroleerde brief en elk eigen document, om terug te vinden. Wat nog op jou wacht, staat in de Inbox.`}
      />
      <AiStatusLine workspaceId={workspace.id} />
      {facets.data ? (
        <p className="letter-counts" aria-live="polite">
          {plural(total, { one: "# document", other: "# documenten" })}
        </p>
      ) : null}
      <ToggleButtonGroup
        className="segmented"
        aria-label={t`Soort`}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[soort ?? "alles"]}
        onSelectionChange={(keys) => {
          const [next] = [...keys];
          go({ soort: next === "brieven" || next === "documenten" ? next : undefined });
        }}
      >
        <ToggleButton id="alles" className="segment">
          {t`Alles`}
        </ToggleButton>
        <ToggleButton id="brieven" className="segment">
          {letters === undefined ? t`Brieven` : t`Brieven (${letters})`}
        </ToggleButton>
        <ToggleButton id="documenten" className="segment">
          {documents === undefined ? t`Documenten` : t`Documenten (${documents})`}
        </ToggleButton>
      </ToggleButtonGroup>
      <ToggleButtonGroup
        className="segmented"
        aria-label={t`Status van de brief`}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[status ?? "alles"]}
        onSelectionChange={(keys) => {
          const [next] = [...keys];
          go({ status: ARCHIVE_STATUSES.find((entry) => entry.url === next)?.url });
        }}
      >
        <ToggleButton id="alles" className="segment">
          {t`Alle statussen`}
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
          <label htmlFor="filter-type">{t`Type`}</label>
          <select
            id="filter-type"
            value={type ?? ""}
            onChange={(event) => go({ type: event.target.value || undefined })}
          >
            <option value="">{t`Alle typen`}</option>
            {typeOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.text}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="filter-person">{t`Van wie`}</label>
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
          <label htmlFor="filter-sender">{t`Afzender`}</label>
          <select
            id="filter-sender"
            value={afzender ?? ""}
            onChange={(event) => go({ afzender: event.target.value || undefined })}
          >
            <option value="">{t`Alle afzenders`}</option>
            {senderOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.text}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="filter-year">{t`Jaar`}</label>
          <select
            id="filter-year"
            value={jaar ? String(jaar) : ""}
            onChange={(event) => go({ jaar: Number(event.target.value) || undefined })}
          >
            <option value="">{t`Alle jaren`}</option>
            {yearOptions.map((option) => (
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
            id="filter-expiring"
            type="checkbox"
            checked={expiring === true}
            onChange={(event) => go({ "loopt-af": event.target.checked ? true : undefined })}
          />
          <label htmlFor="filter-expiring">{t`Loopt af`}</label>
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
      </div>
      {isPending ? <Loading /> : null}
      <ErrorAlert error={error ?? persons.error ?? labels.error ?? facets.error} />
      {data ? (
        <DocumentTable
          workspaceId={workspace.id}
          documents={data}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
          letterStatus="all"
          archive
          caption={t`Archief`}
          empty={
            filtered || expiring ? t`Niets gevonden met deze filters.` : t`Nog niets in je archief.`
          }
          hint={
            filtered || expiring
              ? t`Kies bij een filter een andere waarde, of zet hem op Alle.`
              : t`Brieven komen hier zodra je ze hebt gecontroleerd. Een eigen document toevoegen kan niet in de browser.`
          }
        />
      ) : null}
    </Shell>
  );
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function flag(value: unknown): true | undefined {
  return value === true || value === "true" ? true : undefined;
}

/** The archive's filters from a URL; an unknown value is dropped. Also what `/post` keeps. */
export function validateArchiveSearch(search: Record<string, unknown>): ArchiveSearch {
  const year = Number(search.jaar);
  const kind = search.soort;
  const status = search.status;
  const result: ArchiveSearch = {
    soort: kind === "brieven" || kind === "documenten" ? kind : undefined,
    type: text(search.type),
    persoon: text(search.persoon),
    afzender: text(search.afzender),
    jaar: Number.isInteger(year) && year >= 1900 && year <= 2200 ? year : undefined,
    label: text(search.label),
    status: ARCHIVE_STATUSES.find((entry) => entry.url === status)?.url,
    telaat: flag(search.telaat),
    automatisch: flag(search.automatisch),
    "loopt-af": flag(search["loopt-af"]),
  };
  return Object.fromEntries(
    Object.entries(result).filter(([, value]) => value !== undefined),
  ) as ArchiveSearch;
}

export const archiveRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "archief",
  validateSearch: validateArchiveSearch,
  component: ArchivePage,
});
