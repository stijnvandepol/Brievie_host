import { plural, t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { Fragment, useEffect, useRef, useState } from "react";
import { Button } from "react-aria-components";
import { type DocumentSearch, labelsQuery, personsQuery, searchDocuments } from "../api/queries";
import type { components } from "../api/schema";
import { DocumentTable, typeLabel } from "../components/document-table";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading, MessageBar } from "../components/ui";
import { useSearchRequest } from "../search-term";
import { workspaceRoute } from "./workspace";

type Hit = components["schemas"]["DocumentSearchHitOut"];
type Part = components["schemas"]["SnippetPartOut"];

const MIN_LENGTH = 2;
// Filter changes come in bursts (paging through dates with the arrow keys): one search
// after they stop, not one per change (the endpoint allows 30 a minute).
const FILTER_DELAY_MS = 300;

const DOCUMENT_TYPES = [
  "invoice",
  "assessment",
  "decision",
  "bill",
  "reminder",
  "demand",
  "policy",
  "annual_statement",
  "payslip",
  "inspection",
  "contract",
  "receipt",
  "information",
  "other",
] as const;

type Filters = {
  document_type: string;
  label_id: string;
  person_id: string;
  inbox_state: string;
  date_from: string;
  date_to: string;
};

const NO_FILTERS: Filters = {
  document_type: "",
  label_id: "",
  person_id: "",
  inbox_state: "",
  date_from: "",
  date_to: "",
};

type Outcome = {
  items: Hit[];
  nextOffset: number | null;
  fuzzy: boolean;
  bsnRemoved: boolean;
};

/** The part of the letter that was found: hits in `<mark>`, the rest as plain text. */
function SearchFragment({ parts }: { parts: Part[] }) {
  if (parts.length === 0) return null;
  let position = 0;
  return (
    <p className="letter-fragment">
      {parts.map((part) => {
        const key = position;
        position += part.text.length + 1;
        return part.hit ? (
          <mark key={key}>{part.text}</mark>
        ) : (
          <Fragment key={key}>{part.text}</Fragment>
        );
      })}
    </p>
  );
}

/** A new page per workspace: filters and results of another workspace never carry over. */
function SearchRoute() {
  const { workspace } = searchRoute.useRouteContext();
  return <SearchPage key={workspace.id} />;
}

function SearchPage() {
  usePageTitle(t`Zoeken`);
  const { workspace } = searchRoute.useRouteContext();
  const { term, run } = useSearchRequest();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  // The filters the results are for: `filters` once it has not changed for a moment.
  const [applied, setApplied] = useState<Filters>(NO_FILTERS);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [searching, setSearching] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Only the newest request may change the page.
  const latest = useRef(0);
  const last = useRef<DocumentSearch | null>(null);

  const trimmed = term.trim();
  const tooShort = trimmed.length > 0 && trimmed.length < MIN_LENGTH;
  const filtered = Object.values(filters).some((value) => value !== "");

  useEffect(() => {
    const timer = setTimeout(() => setApplied(filters), FILTER_DELAY_MS);
    return () => clearTimeout(timer);
  }, [filters]);

  // A new term, a repeated search or a changed filter searches again from the start.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `run` repeats the same term on purpose.
  useEffect(() => {
    latest.current += 1;
    const mine = latest.current;
    setError(null);
    setOutcome(null);
    setLoadingMore(false);
    if (trimmed.length < MIN_LENGTH) {
      setSearching(false);
      last.current = null;
      return;
    }
    const request: DocumentSearch = { q: trimmed, ...applied };
    last.current = request;
    setSearching(true);
    searchDocuments(workspace.id, request).then(
      (data) => {
        if (latest.current !== mine) return;
        setOutcome({
          items: data.items,
          nextOffset: data.next_offset ?? null,
          fuzzy: data.fuzzy,
          bsnRemoved: data.bsn_removed,
        });
        setSearching(false);
      },
      (failure: unknown) => {
        if (latest.current !== mine) return;
        setError(failure);
        setSearching(false);
      },
    );
  }, [workspace.id, trimmed, run, applied]);

  async function loadMore() {
    const request = last.current;
    if (!request || !outcome || outcome.nextOffset === null) return;
    const mine = latest.current;
    setLoadingMore(true);
    setError(null);
    try {
      const data = await searchDocuments(workspace.id, { ...request, offset: outcome.nextOffset });
      if (latest.current !== mine) return;
      setOutcome({
        items: [...outcome.items, ...data.items],
        nextOffset: data.next_offset ?? null,
        fuzzy: outcome.fuzzy || data.fuzzy,
        bsnRemoved: outcome.bsnRemoved || data.bsn_removed,
      });
    } catch (failure) {
      if (latest.current === mine) setError(failure);
    } finally {
      if (latest.current === mine) setLoadingMore(false);
    }
  }

  function setFilter(name: keyof Filters, value: string) {
    setFilters((before) => ({ ...before, [name]: value }));
  }

  const count = outcome?.items.length ?? 0;
  const more = outcome !== null && outcome.nextOffset !== null;
  const status = searching
    ? t`Bezig met zoeken…`
    : outcome && count > 0
      ? more
        ? plural(count, {
            one: "# brief geladen, er zijn er meer",
            other: "# brieven geladen, er zijn er meer",
          })
        : plural(count, { one: "# brief gevonden", other: "# brieven gevonden" })
      : "";

  return (
    <Shell workspace={workspace}>
      <PageHeader title={t`Zoeken`} />
      <div className="filter-bar">
        <div className="filter">
          <label htmlFor="search-type">{t`Soort`}</label>
          <select
            id="search-type"
            value={filters.document_type}
            onChange={(event) => setFilter("document_type", event.target.value)}
          >
            <option value="">{t`Alle soorten`}</option>
            {DOCUMENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {typeLabel(type)}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="search-label">{t`Label`}</label>
          <select
            id="search-label"
            value={filters.label_id}
            onChange={(event) => setFilter("label_id", event.target.value)}
          >
            <option value="">{t`Alle labels`}</option>
            {(labels.data ?? []).map((label) => (
              <option key={label.id} value={label.id}>
                {label.name}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="search-person">{t`Persoon`}</label>
          <select
            id="search-person"
            value={filters.person_id}
            onChange={(event) => setFilter("person_id", event.target.value)}
          >
            <option value="">{t`Iedereen`}</option>
            {(persons.data ?? []).map((person) => (
              <option key={person.id} value={person.id}>
                {person.display_name}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor="search-status">{t`Status`}</label>
          <select
            id="search-status"
            value={filters.inbox_state}
            onChange={(event) => setFilter("inbox_state", event.target.value)}
          >
            <option value="">{t`Alle`}</option>
            <option value="review">{t`Controleren`}</option>
            <option value="unassigned">{t`Naam kiezen`}</option>
          </select>
        </div>
        <fieldset className="filter filter-period">
          <legend>{t`Periode`}</legend>
          <div className="filter-period-fields">
            <label>
              <span>{t`Van`}</span>
              <input
                type="date"
                value={filters.date_from}
                onChange={(event) => setFilter("date_from", event.target.value)}
              />
            </label>
            <label>
              <span>{t`Tot`}</span>
              <input
                type="date"
                value={filters.date_to}
                onChange={(event) => setFilter("date_to", event.target.value)}
              />
            </label>
          </div>
        </fieldset>
        {filtered ? (
          <Button className="button" onPress={() => setFilters(NO_FILTERS)}>
            {t`Wis filters`}
          </Button>
        ) : null}
      </div>

      <p role="status" aria-live="polite" className={status ? "search-status" : "visually-hidden"}>
        {status}
      </p>
      <ErrorAlert error={error} />
      {outcome?.bsnRemoved ? (
        <MessageBar intent="info" role="status">
          {t`Zoeken op een BSN kan niet: Brievie bewaart geen BSN's. We hebben het nummer uit je zoekopdracht gehaald.`}
        </MessageBar>
      ) : null}
      {outcome?.fuzzy && count > 0 ? (
        <MessageBar intent="info" role="status">
          {t`Geen exacte treffer. Dit lijkt erop.`}
        </MessageBar>
      ) : null}

      {searching ? <Loading /> : null}

      {trimmed === "" && !searching ? (
        <section className="panel" aria-label={t`Zoekresultaten`}>
          <div className="empty-state">
            <Icon name="search" className="empty-icon" />
            <h2 className="empty-title">{t`Zoek een brief`}</h2>
            <p className="empty-hint">{t`Zoek op afzender, onderwerp, een woord uit de brief, een label of een naam. Het begin van een woord is genoeg.`}</p>
          </div>
        </section>
      ) : null}

      {tooShort ? (
        <MessageBar intent="info" role="status">
          {t`Typ minstens twee tekens.`}
        </MessageBar>
      ) : null}

      {outcome && count === 0 ? (
        <section className="panel" aria-label={t`Zoekresultaten`}>
          <div className="empty-state">
            <h2 className="empty-title">{t`Niets gevonden`}</h2>
            <ul className="empty-steps">
              <li>{t`Gebruik minder woorden.`}</li>
              <li>{t`Probeer een andere spelling of het begin van het woord.`}</li>
            </ul>
            <p>
              <Link to="/w/$workspaceId/archief" params={{ workspaceId: workspace.id }}>
                {t`Bekijk het archief`}
              </Link>
            </p>
            {filtered ? (
              <Button className="button" onPress={() => setFilters(NO_FILTERS)}>
                {t`Wis filters`}
              </Button>
            ) : null}
          </div>
        </section>
      ) : null}

      {outcome && count > 0 ? (
        <>
          <DocumentTable
            workspaceId={workspace.id}
            documents={outcome.items}
            persons={persons.data ?? []}
            labels={labels.data ?? []}
            empty={t`Niets gevonden`}
            caption={t`Zoekresultaten`}
            fragment={(document) => <SearchFragment parts={document.snippet ?? []} />}
          />
          {more ? (
            <Button className="button" isDisabled={loadingMore} onPress={() => void loadMore()}>
              {t`Meer laden`}
            </Button>
          ) : null}
        </>
      ) : null}
    </Shell>
  );
}

export const searchRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  // No search params: the term lives in memory only (see search-term.ts).
  path: "zoeken",
  component: SearchRoute,
});
