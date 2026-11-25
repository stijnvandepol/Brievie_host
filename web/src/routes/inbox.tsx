import { plural, t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";
import { ToggleButton, ToggleButtonGroup } from "react-aria-components";
import {
  documentsQuery,
  duplicatesQuery,
  inboxCountsQuery,
  labelsQuery,
  lingeringQuery,
  membersQuery,
  menuCountsQuery,
  meQuery,
  personsQuery,
  visibilityQuery,
} from "../api/queries";
import type { components } from "../api/schema";
import { AiStatusLine } from "../components/ai-status-line";
import { ConfirmDialog } from "../components/confirm-dialog";
import { Addressees, LetterDateText, SenderText, TypeText } from "../components/document-table";
import {
  assignWord,
  handOverSentence,
  losesLetter,
  type OwnSight,
  ownSight,
} from "../components/hand-over";
import { LabelChips } from "../components/label-chip";
import { LetterStatusBadge } from "../components/letter-status";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { PrivateLock } from "../components/private-lock";
import { SuggestionMark } from "../components/suggestion-list";
import { Badge, EmptyState, Loading, MessageBar } from "../components/ui";
import { clearDismissed, isDismissedPending } from "../duplicate-notice";
import { takeLetterGone } from "../lost-letter";
import { departmentsTitle } from "./departments";
import { workspaceRoute } from "./workspace";

type DocumentSummary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];
type LabelOut = components["schemas"]["LabelOut"];
type Counts = components["schemas"]["InboxCountsOut"];
type Duplicate = components["schemas"]["DuplicateOut"];

/**
 * The page "Inbox" (route and API keep the name inbox). For the person, `review` and
 * `unassigned` are things to do, `new` is a letter still being read: nothing to do yet.
 */
function ActionBadge({ state, departments }: { state: string; departments: boolean }) {
  if (state === "unassigned") return <Badge intent="warning">{assignWord(departments)}</Badge>;
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
  onGone,
  sight,
  departments,
}: {
  workspaceId: string;
  document: DocumentSummary;
  persons: Person[];
  labels: LabelOut[];
  canEdit: boolean;
  /** Called once the letter is on a name, while the row is still in the list. */
  onAssigned: () => void;
  /** Called when the caller can no longer read the letter they assigned. */
  onGone: () => void;
  sight: OwnSight | null;
  /** The word for departments while they are on ("Afdelingen", "Groepen"). */
  departments: string | undefined;
}) {
  const setAddressees = useSetAddressees(workspaceId, document.id, onAssigned);
  const [handOver, setHandOver] = useState<string[] | null>(null);
  const state = document.inbox_state ?? "";
  const before = document.addressee_ids ?? [];
  async function save(ids: string[]) {
    const answer = await setAddressees.mutateAsync(ids);
    if (answer.can_read === false) onGone();
  }
  function choose(ids: string[]) {
    if (
      document.visibility !== "restricted" &&
      losesLetter(sight, ids, persons, departments !== undefined)
    ) {
      setHandOver(ids);
      return;
    }
    save(ids).catch(() => {
      // Shown under the picker (setAddressees.error).
    });
  }
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
          <PrivateLock
            visibility={document.visibility}
            readerNames={document.reader_names}
            variant="row"
          />
          <SuggestionMark count={document.open_suggestion_count} />
          {/* Only a checked letter that still needs a name: a late payment must not look like
              any other. The rows that still need checking keep their action word alone. */}
          {state === "unassigned" ? (
            <LetterStatusBadge
              status={document.letter_status}
              overdue={document.overdue}
              waitingUntil={document.waiting_until}
            />
          ) : null}
          {(document.open_duplicate_count ?? 0) > 0 ? (
            <Badge intent="warning">{t`Dubbel?`}</Badge>
          ) : null}
        </span>
      </td>
      <td>
        <span className="cell-todo">
          <ActionBadge state={state} departments={departments !== undefined} />
          {state === "unassigned" ? (
            <PersonPicker
              persons={persons}
              selectedIds={before}
              onChange={choose}
              disabled={!canEdit || setAddressees.isPending}
              departments={departments}
              newPersonIn={canEdit ? workspaceId : undefined}
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
        {sight && handOver ? (
          <ConfirmDialog
            isOpen
            onOpenChange={(open) => {
              if (!open) setHandOver(null);
            }}
            title={t`Brief toewijzen?`}
            confirmLabel={t`Toewijzen`}
            tone="primary"
            onConfirm={() => save(handOver)}
          >
            <p>{handOverSentence(sight, before, handOver, persons)}</p>
          </ConfirmDialog>
        ) : null}
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

/** "Voor mij", one per department, "Zonder naam": which part of the inbox is shown. */
type Chip = { key: string; label: string; keep: (document: DocumentSummary) => boolean };

function chipsFor(sight: OwnSight | null, everyoneSeesAll: boolean): Chip[] {
  if (!sight || everyoneSeesAll) return [];
  if (sight.departments.length === 0 && !sight.sorts) return [];
  const own = sight.ownPersonId;
  const chips: Chip[] = [{ key: "all", label: t`Alles`, keep: () => true }];
  if (own) {
    chips.push({
      key: "mine",
      label: t`Voor mij`,
      keep: (document) => (document.addressee_ids ?? []).includes(own),
    });
  }
  for (const department of sight.departments) {
    chips.push({
      key: department.id,
      label: department.display_name,
      keep: (document) => (document.addressee_ids ?? []).includes(department.id),
    });
  }
  if (sight.sorts) {
    chips.push({
      key: "unnamed",
      label: t`Zonder naam`,
      keep: (document) => document.inbox_state === "unassigned",
    });
  }
  return chips;
}

function TodoList({
  workspaceId,
  canEdit,
  role,
  onGone,
}: {
  workspaceId: string;
  canEdit: boolean;
  role: string;
  onGone: () => void;
}) {
  const { data: all, error, isFetching } = useTodoList(workspaceId);
  const persons = useQuery(personsQuery(workspaceId));
  const labels = useQuery(labelsQuery(workspaceId));
  const visibility = useQuery(visibilityQuery(workspaceId));
  const members = useQuery(membersQuery(workspaceId));
  const me = useQuery(meQuery);
  const sight = ownSight(
    visibility.data,
    role,
    me.data?.id,
    members.data ?? [],
    persons.data ?? [],
  );
  const departmentsOn = visibility.data?.departments_enabled === true;
  const chips = chipsFor(sight, visibility.data?.everyone_sees_all ?? true);
  const [chip, setChip] = useState("all");
  const keep = chips.find((candidate) => candidate.key === chip)?.keep ?? (() => true);
  const data = all?.filter(keep);
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
  const chipBar =
    chips.length > 0 ? (
      <ToggleButtonGroup
        className="segmented"
        aria-label={t`Welke brieven`}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[chip]}
        onSelectionChange={(keys) => {
          const [next] = [...keys];
          if (typeof next === "string") setChip(next);
        }}
      >
        {chips.map((candidate) => (
          <ToggleButton key={candidate.key} id={candidate.key} className="segment">
            {candidate.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    ) : null;
  if (data.length === 0) {
    return (
      <>
        {chipBar}
        <section className="panel" ref={emptyPanel} tabIndex={-1}>
          <EmptyState
            icon="success"
            hint={
              <>
                {t`Scan een nieuwe brief met de Brievie-app. Eerdere brieven vind je bij`}{" "}
                <Link to="/w/$workspaceId/archief" params={{ workspaceId }}>
                  {t`Archief`}
                </Link>
                .
              </>
            }
          >
            {t`Je bent klaar. Er is niets te doen.`}
          </EmptyState>
        </section>
      </>
    );
  }
  return (
    <>
      {chipBar}
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1). */}
      <section className="panel table-scroll" aria-label={t`Inbox`} tabIndex={0}>
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
                onGone={onGone}
                sight={sight}
                departments={
                  departmentsOn ? departmentsTitle(visibility.data?.kind ?? "business") : undefined
                }
              />
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

/** The numbers per kind of work, in the words of the badges; nothing when nothing waits. */
function CountsLine({ counts, departments }: { counts: Counts; departments: boolean }) {
  const assign = assignWord(departments);
  const parts = [
    counts.review > 0 ? t`Controleren: ${counts.review}` : "",
    counts.unassigned > 0 ? `${assign}: ${counts.unassigned}` : "",
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

type LingeringLetter = components["schemas"]["LingeringLetterOut"];

/** One letter that lies too long: its sender (a link), how long, and for whom. */
function LingeringItem({
  workspaceId,
  letter,
  persons,
  opened,
}: {
  workspaceId: string;
  letter: LingeringLetter;
  persons: Person[];
  opened: boolean;
}) {
  const days = letter.days;
  const age = opened
    ? plural(days, { one: "# dag niet geopend", other: "# dagen niet geopend" })
    : plural(days, { one: "ligt er # dag", other: "ligt er # dagen" });
  return (
    <li className="lingering-item">
      <Link
        to="/w/$workspaceId/post/$documentId"
        params={{ workspaceId, documentId: letter.id }}
        className="cell-strong"
      >
        <SenderText document={letter} />
      </Link>
      <span className="lingering-age">{age}</span>
      <Addressees persons={persons} ids={letter.addressee_ids ?? []} />
    </li>
  );
}

/**
 * "Blijft liggen" (spec S 6.2): for the sorters and admins of an organisation, the letters
 * too long in the Inbox and the confirmed letters none of whose addressees opened them.
 * Only letters the caller may see; one line when nothing lingers.
 */
function Lingering({ workspaceId }: { workspaceId: string }) {
  const counts = useQuery(menuCountsQuery(workspaceId));
  const shows = counts.data?.shows_lingering === true;
  const { data, error } = useQuery({ ...lingeringQuery(workspaceId), enabled: shows });
  const persons = useQuery({ ...personsQuery(workspaceId), enabled: shows });
  const headingId = useId();
  if (!shows) return null;
  const inbox = data?.too_long_in_inbox ?? [];
  const unopened = data?.not_opened ?? [];
  const empty = data !== undefined && inbox.length === 0 && unopened.length === 0;
  return (
    <section className="panel" aria-labelledby={headingId}>
      <div className="panel-head">
        <h2 id={headingId}>
          {t`Blijft liggen`}{" "}
          {inbox.length + unopened.length > 0 ? (
            <Badge intent="warning">{inbox.length + unopened.length}</Badge>
          ) : null}
        </h2>
      </div>
      <ErrorAlert error={error} />
      {data === undefined && !error ? <Loading /> : null}
      {empty ? <p className="panel-note">{t`Er blijft niets liggen.`}</p> : null}
      {inbox.length > 0 ? (
        <>
          <h3 className="lingering-title">
            {plural(data?.inbox_days ?? 0, {
              one: "In de Inbox te lang (# dag of meer)",
              other: "In de Inbox te lang (# dagen of meer)",
            })}
          </h3>
          <ul className="lingering-list">
            {inbox.map((letter) => (
              <LingeringItem
                key={letter.id}
                workspaceId={workspaceId}
                letter={letter}
                persons={persons.data ?? []}
                opened={false}
              />
            ))}
          </ul>
        </>
      ) : null}
      {unopened.length > 0 ? (
        <>
          <h3 className="lingering-title">
            {plural(data?.unopened_days ?? 0, {
              one: "Nog niet geopend (# dag of meer)",
              other: "Nog niet geopend (# dagen of meer)",
            })}
          </h3>
          <ul className="lingering-list">
            {unopened.map((letter) => (
              <LingeringItem
                key={letter.id}
                workspaceId={workspaceId}
                letter={letter}
                persons={persons.data ?? []}
                opened
              />
            ))}
          </ul>
        </>
      ) : null}
    </section>
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
  usePageTitle(t`Inbox`);
  const { workspace } = inboxRoute.useRouteContext();
  const { data: counts, error } = useQuery(inboxCountsQuery(workspace.id));
  const { data: visibility } = useQuery(visibilityQuery(workspace.id));
  // A letter assigned elsewhere (here, or on its own page) that the caller can no longer open.
  const [gone, setGone] = useState(() => takeLetterGone(workspace.id));
  // "Het zijn twee brieven" brought the person here: say it once.
  const [dismissed] = useState(() => isDismissedPending(workspace.id));
  useEffect(() => clearDismissed(), []);
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Inbox`}
        description={t`Brieven die je nog moet nakijken of een naam moet geven. Wat je daarna moet doen, staat bij Mijn taken.`}
      />
      <AiStatusLine workspaceId={workspace.id} />
      <ErrorAlert error={error} />
      {dismissed ? (
        <MessageBar intent="success" role="status">
          {t`Goed. We vragen het niet meer voor deze twee brieven.`}
        </MessageBar>
      ) : null}
      {gone ? (
        <MessageBar intent="info" role="status">
          {t`Je kunt deze brief niet meer openen.`}
        </MessageBar>
      ) : null}
      {counts ? (
        <CountsLine counts={counts} departments={visibility?.departments_enabled === true} />
      ) : null}
      <Lingering workspaceId={workspace.id} />
      {counts ? <Duplicates workspaceId={workspace.id} count={counts.duplicate ?? 0} /> : null}
      <TodoList
        workspaceId={workspace.id}
        canEdit={workspace.role !== "viewer"}
        role={workspace.role}
        onGone={() => setGone(true)}
      />
    </Shell>
  );
}

export const inboxRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  // The path stays "inbox" (bookmarks); the page is called "Inbox".
  path: "inbox",
  component: InboxPage,
});
