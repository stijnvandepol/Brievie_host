import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { documentsQuery, inboxCountsQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { formatLetterDate, typeLabel } from "../components/document-table";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { workspaceRoute } from "./workspace";

type DocumentSummary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];

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
}: {
  workspaceId: string;
  document: DocumentSummary;
  tab: InboxTab;
  persons: Person[];
}) {
  const setAddressees = useSetAddressees(workspaceId, document.id);
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
        {tab === "unassigned" ? (
          <>
            <PersonPicker
              persons={persons}
              selectedIds={document.addressee_ids}
              onChange={(ids) => setAddressees.mutate(ids)}
              disabled={setAddressees.isPending}
            />
            <ErrorAlert error={setAddressees.error} />
          </>
        ) : null}
        {tab === "review" ? t`Controleer deze brief in de app` : null}
        {tab === "new" ? t`Deze brief wordt nog gelezen.` : null}
      </td>
    </tr>
  );
}

function InboxList({ workspaceId, tab }: { workspaceId: string; tab: InboxTab }) {
  const documents = useQuery(documentsQuery(workspaceId, { inbox_state: tab }));
  const persons = useQuery(personsQuery(workspaceId));
  if (documents.isPending) return <p>{t`Even laden...`}</p>;
  if (documents.error) return <ErrorAlert error={documents.error} />;
  if (documents.data.length === 0) {
    return (
      <section className="panel">
        <p className="panel-note">{t`Hier is niets te doen.`}</p>
      </section>
    );
  }
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
    <section className="panel table-scroll" aria-label={t`Inbox`} tabIndex={0}>
      <ErrorAlert error={persons.error} />
      <table className="table table-wide">
        <thead>
          <tr>
            <th scope="col">{t`Afzender`}</th>
            <th scope="col">{t`Soort`}</th>
            <th scope="col">{t`Datum brief`}</th>
            <th scope="col">{tab === "unassigned" ? t`Op naam van` : t`Wat te doen`}</th>
          </tr>
        </thead>
        <tbody>
          {documents.data.map((document) => (
            <InboxRow
              key={document.id}
              workspaceId={workspaceId}
              document={document}
              tab={tab}
              persons={persons.data ?? []}
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
  const labels: Record<InboxTab, string> = {
    review: t`Te controleren`,
    unassigned: t`Zonder eigenaar`,
    new: t`Nieuw`,
  };

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{t`Inbox`}</h1>
      </header>
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
                {labels[id]} <span className="tag">{counts[id]}</span>
              </Tab>
            ))}
          </TabList>
          <TabPanel id={tab} className="tab-panel">
            <InboxList workspaceId={workspace.id} tab={tab} />
          </TabPanel>
        </Tabs>
      ) : (
        !error && <p>{t`Even laden...`}</p>
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
