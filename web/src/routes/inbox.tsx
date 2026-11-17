import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { documentsQuery, inboxCountsQuery, labelsQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { formatLetterDate, typeLabel } from "../components/document-table";
import { LabelChips } from "../components/label-chip";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { SuggestionMark } from "../components/suggestion-list";
import { EmptyState, Loading } from "../components/ui";
import { workspaceRoute } from "./workspace";

type DocumentSummary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];
type LabelOut = components["schemas"]["LabelOut"];

const TABS = ["review", "unassigned", "new"] as const;
type InboxTab = (typeof TABS)[number];

function isTab(value: unknown): value is InboxTab {
  return TABS.some((tab) => tab === value);
}

function InboxRow({
  workspaceId,
  document,
  tab,
  persons,
  labels,
  canEdit,
  onAssigned,
}: {
  workspaceId: string;
  document: DocumentSummary;
  tab: InboxTab;
  persons: Person[];
  labels: LabelOut[];
  canEdit: boolean;
  /** Called once the letter is on a name, while the row is still in the list. */
  onAssigned: () => void;
}) {
  const setAddressees = useSetAddressees(workspaceId, document.id, onAssigned);
  return (
    <tr>
      <th scope="row" className="cell-strong">
        <Link
          to="/w/$workspaceId/post/$documentId"
          params={{ workspaceId, documentId: document.id }}
        >
          {document.sender || t`Onbekende afzender`}
        </Link>
      </th>
      <td>{typeLabel(document.document_type)}</td>
      <td className="num">{document.letter_date ? formatLetterDate(document.letter_date) : ""}</td>
      <td>
        <span className="cell-badges">
          <LabelChips labels={labels} ids={document.label_ids ?? []} />
          <SuggestionMark count={document.open_suggestion_count} />
        </span>
      </td>
      <td>
        {tab === "unassigned" ? (
          <>
            <PersonPicker
              persons={persons}
              selectedIds={document.addressee_ids ?? []}
              onChange={(ids) => setAddressees.mutate(ids)}
              disabled={!canEdit || setAddressees.isPending}
            />
            <ErrorAlert error={setAddressees.error} />
          </>
        ) : null}
        {tab === "review" ? (
          <span className="cell-hint">{t`Controleer deze brief in de app.`}</span>
        ) : null}
        {tab === "new" ? (
          <span className="cell-hint">{t`Deze brief wordt nog gelezen.`}</span>
        ) : null}
      </td>
    </tr>
  );
}

function InboxList({
  workspaceId,
  tab,
  canEdit,
  poll,
}: {
  workspaceId: string;
  tab: InboxTab;
  canEdit: boolean;
  /** Fetch the list again by itself (letters are still being read). */
  poll: boolean;
}) {
  const documents = useQuery(documentsQuery(workspaceId, { inbox_state: tab }, { poll }));
  const persons = useQuery(personsQuery(workspaceId));
  const labels = useQuery(labelsQuery(workspaceId));
  const body = useRef<HTMLTableSectionElement>(null);
  const emptyPanel = useRef<HTMLElement>(null);
  // The row a letter was put on a name from: when it leaves the list, focus must not fall to <body>.
  const assigned = useRef<{ id: string; index: number } | null>(null);
  const { data, isFetching } = documents;
  useEffect(() => {
    const pending = assigned.current;
    if (!pending || !data || isFetching) return;
    assigned.current = null;
    if (data.some((document) => document.id === pending.id)) return;
    if (data.length === 0) {
      emptyPanel.current?.focus();
      return;
    }
    const row = body.current?.querySelectorAll("tr")[Math.min(pending.index, data.length - 1)];
    row?.querySelector<HTMLElement>(".picker-button")?.focus();
  }, [data, isFetching]);
  if (documents.isPending) return <Loading />;
  if (documents.error) return <ErrorAlert error={documents.error} />;
  if (documents.data.length === 0) {
    return (
      <section className="panel" ref={emptyPanel} tabIndex={-1}>
        <EmptyState
          icon="success"
          hint={t`Komt er een brief binnen die je moet controleren of op een naam moet zetten, dan staat die hier.`}
        >
          {t`Hier is niets te doen.`}
        </EmptyState>
      </section>
    );
  }
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
    <section className="panel table-scroll" aria-label={t`Inbox`} tabIndex={0}>
      <ErrorAlert error={persons.error ?? labels.error} />
      <table className="table table-wide">
        <thead>
          <tr>
            <th scope="col">{t`Afzender`}</th>
            <th scope="col">{t`Soort`}</th>
            <th scope="col">{t`Datum brief`}</th>
            <th scope="col">{t`Labels`}</th>
            <th scope="col">{tab === "unassigned" ? t`Op naam van` : t`Wat te doen`}</th>
          </tr>
        </thead>
        <tbody ref={body}>
          {documents.data.map((document, index) => (
            <InboxRow
              key={document.id}
              workspaceId={workspaceId}
              document={document}
              tab={tab}
              persons={persons.data ?? []}
              labels={labels.data ?? []}
              canEdit={canEdit}
              onAssigned={() => {
                assigned.current = { id: document.id, index };
              }}
            />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function InboxPage() {
  usePageTitle(t`Inbox`);
  const { workspace } = inboxRoute.useRouteContext();
  const search = inboxRoute.useSearch();
  const navigate = useNavigate();
  const { data: counts, error } = useQuery(inboxCountsQuery(workspace.id));

  // Without a tab in the URL: the first one that has something to do.
  const tab: InboxTab | undefined =
    search.tab ??
    (counts ? (TABS.find((candidate) => counts[candidate] > 0) ?? TABS[0]) : undefined);
  // Once a tab is chosen it is in the URL, so it does not jump when that tab becomes empty.
  const chosen = search.tab === undefined ? tab : undefined;
  useEffect(() => {
    if (!chosen) return;
    void navigate({
      to: "/w/$workspaceId/inbox",
      params: { workspaceId: workspace.id },
      search: { tab: chosen },
      replace: true,
    });
  }, [chosen, navigate, workspace.id]);
  const labels: Record<InboxTab, string> = {
    review: t`Te controleren`,
    unassigned: t`Op niemands naam`,
    new: t`Nieuw`,
  };

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Inbox`}
        description={t`Post die op jou wacht: brieven om te controleren of op een naam te zetten.`}
      />
      <ErrorAlert error={error} />
      {tab && counts ? (
        <Tabs
          selectedKey={tab}
          onSelectionChange={(key) => {
            if (!isTab(key)) return;
            void navigate({
              to: "/w/$workspaceId/inbox",
              params: { workspaceId: workspace.id },
              search: { tab: key },
            });
          }}
        >
          <TabList aria-label={t`Inbox`} className="tabs">
            {TABS.map((id) => (
              <Tab key={id} id={id} className="tab">
                {labels[id]} <span className="tab-count">{counts[id]}</span>
              </Tab>
            ))}
          </TabList>
          <TabPanel id={tab} className="tab-panel">
            <InboxList
              workspaceId={workspace.id}
              tab={tab}
              canEdit={workspace.role !== "viewer"}
              poll={tab === "new"}
            />
          </TabPanel>
        </Tabs>
      ) : (
        !error && <Loading />
      )}
    </Shell>
  );
}

export const inboxRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "inbox",
  validateSearch: (search: Record<string, unknown>): { tab?: InboxTab } =>
    isTab(search.tab) ? { tab: search.tab } : {},
  component: InboxPage,
});
