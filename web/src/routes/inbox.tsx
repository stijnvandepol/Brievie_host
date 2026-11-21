import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";
import {
  documentsQuery,
  duplicatesQuery,
  inboxCountsQuery,
  labelsQuery,
  personsQuery,
} from "../api/queries";
import type { components } from "../api/schema";
import { AiStatusLine } from "../components/ai-status-line";
import { LetterDateText, SenderText, TypeText } from "../components/document-table";
import { LabelChips } from "../components/label-chip";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { SuggestionMark } from "../components/suggestion-list";
import { Badge, EmptyState, Loading, MessageBar } from "../components/ui";
import { clearDismissed, isDismissedPending } from "../duplicate-notice";
import { workspaceRoute } from "./workspace";

type DocumentSummary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];
type LabelOut = components["schemas"]["LabelOut"];
type Counts = components["schemas"]["InboxCountsOut"];
type Duplicate = components["schemas"]["DuplicateOut"];

/**
 * The page "Te doen" (route and API keep the name inbox). For the person, `review` and
 * `unassigned` are things to do, `new` is a letter still being read: nothing to do yet.
 */
function ActionBadge({ state }: { state: string }) {
  if (state === "unassigned") return <Badge intent="warning">{t`Naam kiezen`}</Badge>;
  if (state === "new") return <Badge intent="info">{t`Bezig…`}</Badge>;
  return <Badge intent="warning">{t`Controleren`}</Badge>;
}

function InboxRow({
  workspaceId,
  document,
  persons,
  labels,
  canEdit,
  onAssigned,
}: {
  workspaceId: string;
  document: DocumentSummary;
  persons: Person[];
  labels: LabelOut[];
  canEdit: boolean;
  /** Called once the letter is on a name, while the row is still in the list. */
  onAssigned: () => void;
}) {
  const setAddressees = useSetAddressees(workspaceId, document.id, onAssigned);
  const state = document.inbox_state ?? "";
  return (
    <tr>
      <th scope="row" className="cell-strong">
        <Link
          to="/w/$workspaceId/post/$documentId"
          params={{ workspaceId, documentId: document.id }}
        >
          <SenderText document={document} />
        </Link>
      </th>
      <td>
        <TypeText document={document} />
      </td>
      <td className="num">
        <LetterDateText document={document} />
      </td>
      <td>
        <span className="cell-badges">
          <LabelChips labels={labels} ids={document.label_ids ?? []} />
          <SuggestionMark count={document.open_suggestion_count} />
          {(document.open_duplicate_count ?? 0) > 0 ? (
            <Badge intent="warning">{t`Dubbel?`}</Badge>
          ) : null}
        </span>
      </td>
      <td>
        <span className="cell-todo">
          <ActionBadge state={state} />
          {state === "unassigned" ? (
            <PersonPicker
              persons={persons}
              selectedIds={document.addressee_ids ?? []}
              onChange={(ids) => setAddressees.mutate(ids)}
              disabled={!canEdit || setAddressees.isPending}
            />
          ) : null}
          {state === "review" ? (
            <span className="cell-hint">
              {document.status === "failed"
                ? t`Brievie kon deze brief niet goed lezen. Controleer hem in de app.`
                : t`Controleer deze brief in de app.`}
            </span>
          ) : null}
          {state === "new" ? (
            <span className="cell-hint">{t`Brievie leest deze brief nog. Je hoeft niets te doen.`}</span>
          ) : null}
        </span>
        {state === "unassigned" ? <ErrorAlert error={setAddressees.error} /> : null}
      </td>
    </tr>
  );
}

const newestFirst = (a: DocumentSummary, b: DocumentSummary) =>
  b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id);

/** What needs the person first (newest first), letters still being read last. */
function useTodoList(workspaceId: string) {
  const review = useQuery(documentsQuery(workspaceId, { inbox_state: "review" }));
  const unassigned = useQuery(documentsQuery(workspaceId, { inbox_state: "unassigned" }));
  const reading = useQuery(documentsQuery(workspaceId, { inbox_state: "new" }, { poll: true }));

  // A letter that is read moves on to Controleren or Naam kiezen: fetch those again.
  const readingIds = reading.data?.map((document) => document.id).join(",");
  const previous = useRef(readingIds);
  const { refetch: refetchReview } = review;
  const { refetch: refetchUnassigned } = unassigned;
  useEffect(() => {
    const before = previous.current;
    previous.current = readingIds;
    if (before === undefined || readingIds === undefined) return;
    const now = new Set(readingIds.split(","));
    if (before.split(",").some((id) => id && !now.has(id))) {
      void refetchReview();
      void refetchUnassigned();
    }
  }, [readingIds, refetchReview, refetchUnassigned]);

  const data =
    review.data && unassigned.data && reading.data
      ? [...[...review.data, ...unassigned.data].sort(newestFirst), ...reading.data]
      : undefined;
  return {
    data,
    error: review.error ?? unassigned.error ?? reading.error,
    isFetching: review.isFetching || unassigned.isFetching || reading.isFetching,
  };
}

function TodoList({ workspaceId, canEdit }: { workspaceId: string; canEdit: boolean }) {
  const { data, error, isFetching } = useTodoList(workspaceId);
  const persons = useQuery(personsQuery(workspaceId));
  const labels = useQuery(labelsQuery(workspaceId));
  const body = useRef<HTMLTableSectionElement>(null);
  const emptyPanel = useRef<HTMLElement>(null);
  // The row a letter was put on a name from: when it leaves the list, focus must not fall to <body>.
  const assigned = useRef<{ id: string; index: number } | null>(null);
  useEffect(() => {
    const pending = assigned.current;
    if (!pending || !data || isFetching) return;
    assigned.current = null;
    if (data.some((document) => document.id === pending.id)) return;
    if (data.length === 0) {
      emptyPanel.current?.focus();
      return;
    }
    // The row that takes its place: its picker, or its sender when it has no picker.
    const row = body.current?.querySelectorAll("tr")[Math.min(pending.index, data.length - 1)];
    (row?.querySelector<HTMLElement>(".picker-button") ?? row?.querySelector("a"))?.focus();
  }, [data, isFetching]);
  if (error) return <ErrorAlert error={error} />;
  if (!data) return <Loading />;
  if (data.length === 0) {
    return (
      <section className="panel" ref={emptyPanel} tabIndex={-1}>
        <EmptyState
          icon="success"
          hint={
            <>
              {t`Scan een nieuwe brief met de Brievie-app. Eerdere brieven vind je bij`}{" "}
              <Link to="/w/$workspaceId/post" params={{ workspaceId }}>
                {t`Alle post`}
              </Link>
              .
            </>
          }
        >
          {t`Je bent klaar. Er is niets te doen.`}
        </EmptyState>
      </section>
    );
  }
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
    <section className="panel table-scroll" aria-label={t`Te doen`} tabIndex={0}>
      <ErrorAlert error={persons.error ?? labels.error} />
      <table className="table table-wide">
        <thead>
          <tr>
            <th scope="col">{t`Afzender`}</th>
            <th scope="col">{t`Soort`}</th>
            <th scope="col">{t`Datum brief`}</th>
            <th scope="col">{t`Labels`}</th>
            <th scope="col">{t`Wat te doen`}</th>
          </tr>
        </thead>
        <tbody ref={body}>
          {data.map((document, index) => (
            <InboxRow
              key={document.id}
              workspaceId={workspaceId}
              document={document}
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

/** The numbers per kind of work, in the words of the badges; nothing when nothing waits. */
function CountsLine({ counts }: { counts: Counts }) {
  const parts = [
    counts.review > 0 ? t`Controleren: ${counts.review}` : "",
    counts.unassigned > 0 ? t`Naam kiezen: ${counts.unassigned}` : "",
    counts.new > 0 ? t`Bezig: ${counts.new}` : "",
    (counts.duplicate ?? 0) > 0 ? t`Dubbel?: ${counts.duplicate}` : "",
  ].filter(Boolean);
  return parts.length > 0 ? <p className="muted">{parts.join(" · ")}</p> : null;
}

/** One pair: both senders and dates, and how alike they are; the link opens the pair. */
function DuplicateItem({ workspaceId, pair }: { workspaceId: string; pair: Duplicate }) {
  const similarity = pair.text_similarity;
  return (
    <li>
      <Link
        to="/w/$workspaceId/dubbel/$duplicateId"
        params={{ workspaceId, duplicateId: pair.id }}
        className="duplicate-link"
      >
        <span className="duplicate-letters">
          {[pair.first, pair.second].map((letter) => (
            <span key={letter.id} className="duplicate-letter">
              <span className="cell-strong">
                <SenderText document={letter} />
              </span>{" "}
              <span className="muted">
                <LetterDateText document={letter} />
              </span>
            </span>
          ))}
        </span>
        <span className="muted">{t`Lijkt op elkaar voor ${similarity}%`}</span>
      </Link>
    </li>
  );
}

/** "Dubbel?": pairs of letters that are probably one letter scanned twice. Only when there are any. */
function Duplicates({ workspaceId, count }: { workspaceId: string; count: number }) {
  const { data, error } = useQuery({ ...duplicatesQuery(workspaceId), enabled: count > 0 });
  const headingId = useId();
  if (count === 0) return null;
  return (
    <section className="panel" aria-labelledby={headingId}>
      <div className="panel-head">
        <h2 id={headingId}>
          {t`Dubbel?`} <Badge intent="warning">{count}</Badge>
        </h2>
      </div>
      <p className="panel-note">
        {t`Deze brieven lijken twee keer gescand. Kijk of het dezelfde brief is.`}
      </p>
      <ErrorAlert error={error} />
      {data ? (
        <ul className="duplicate-list">
          {data.map((pair) => (
            <DuplicateItem key={pair.id} workspaceId={workspaceId} pair={pair} />
          ))}
        </ul>
      ) : error ? null : (
        <Loading />
      )}
    </section>
  );
}

function InboxPage() {
  usePageTitle(t`Te doen`);
  const { workspace } = inboxRoute.useRouteContext();
  const { data: counts, error } = useQuery(inboxCountsQuery(workspace.id));
  // "Het zijn twee brieven" brought the person here: say it once.
  const [dismissed] = useState(() => isDismissedPending(workspace.id));
  useEffect(() => clearDismissed(), []);
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Te doen`}
        description={t`Brieven waar jij nog iets mee moet doen. Al je brieven, ook de afgehandelde, staan bij Alle post.`}
      />
      <AiStatusLine workspaceId={workspace.id} />
      <ErrorAlert error={error} />
      {dismissed ? (
        <MessageBar intent="success" role="status">
          {t`Goed. We vragen het niet meer voor deze twee brieven.`}
        </MessageBar>
      ) : null}
      {counts ? <CountsLine counts={counts} /> : null}
      {counts ? <Duplicates workspaceId={workspace.id} count={counts.duplicate ?? 0} /> : null}
      <TodoList workspaceId={workspace.id} canEdit={workspace.role !== "viewer"} />
    </Shell>
  );
}

export const inboxRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  // The path stays "inbox" (bookmarks); the page is called "Te doen".
  path: "inbox",
  component: InboxPage,
});
