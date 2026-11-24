import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { visibilityQuery } from "../api/queries";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { assignWord } from "./hand-over";
import { LabelChips } from "./label-chip";
import { LetterStatusBadge } from "./letter-status";
import { PrivateLock } from "./private-lock";
import { SuggestionMark } from "./suggestion-list";
import { Badge, EmptyState, type Intent, Personas, useMediaQuery } from "./ui";

type DocumentSummary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];
type LabelOut = components["schemas"]["LabelOut"];

const date = dateFormat({ dateStyle: "medium" });

export function typeLabel(type: string): string {
  switch (type) {
    case "invoice":
      return t`Factuur`;
    case "assessment":
      return t`Aanslag`;
    case "decision":
      return t`Beschikking`;
    case "bill":
      return t`Rekening`;
    case "reminder":
      return t`Herinnering`;
    case "demand":
      return t`Aanmaning`;
    case "policy":
      return t`Polis`;
    case "annual_statement":
      return t`Jaaropgave`;
    case "payslip":
      return t`Loonstrook`;
    case "inspection":
      return t`Keuring`;
    case "contract":
      return t`Contract`;
    case "receipt":
      return t`Bonnetje`;
    case "information":
      return t`Informatie`;
    case "other":
      return t`Overig`;
    default:
      return "";
  }
}

export function statusLabel(status: string): string {
  switch (status) {
    case "processing":
      return t`Bezig…`;
    case "needs_review":
      return t`Controleren`;
    case "done":
      return t`Klaar`;
    case "failed":
      return t`Verwerken mislukt`;
    default:
      return status;
  }
}

function statusIntent(status: string): Intent | "brand" | undefined {
  switch (status) {
    case "processing":
      return "info";
    case "needs_review":
      return "warning";
    case "done":
      return "success";
    case "failed":
      return "error";
    default:
      return undefined;
  }
}

/** The status of a letter as a badge: its word, a tint and an icon. A read letter that still
 * waits for a name says so, in the word "Inbox" uses; "Klaar" only when nothing waits. */
export function StatusBadge({
  status,
  inboxState,
  departments = false,
}: {
  status: string;
  inboxState?: string;
  /** Departments are on: such a letter waits to be assigned ("Toewijzen"). */
  departments?: boolean;
}) {
  if (status === "done" && inboxState === "unassigned") {
    return <Badge intent="warning">{assignWord(departments)}</Badge>;
  }
  return <Badge intent={statusIntent(status)}>{statusLabel(status)}</Badge>;
}

/** Whether Brievie handled (part of) this letter itself: confirmed it, or put a label or person on it. */
type AutomaticFields = Pick<
  DocumentSummary,
  "confirmed_by_ai" | "auto_label_ids" | "auto_addressee_ids" | "auto_rule_label_ids"
>;

export function isAutomatic(document: AutomaticFields): boolean {
  return (
    document.confirmed_by_ai === true ||
    (document.auto_label_ids ?? []).length > 0 ||
    (document.auto_addressee_ids ?? []).length > 0
  );
}

/** Whether the AI itself chose something on this letter (not only a rule's label). */
function byAi(document: AutomaticFields): boolean {
  const byRule = document.auto_rule_label_ids ?? [];
  return (
    document.confirmed_by_ai === true ||
    (document.auto_addressee_ids ?? []).length > 0 ||
    (document.auto_label_ids ?? []).some((id) => !byRule.includes(id))
  );
}

/**
 * The mark of a letter Brievie handled itself: text in the same tag as the status, never
 * colour alone. "door regel" when only a rule's label was put on (the AI chose nothing).
 */
export function AutomaticBadge({ document }: { document: AutomaticFields }) {
  if (!isAutomatic(document)) return null;
  return (
    <Badge icon="info">{byAi(document) ? t`Automatisch door AI` : t`Automatisch door regel`}</Badge>
  );
}

/** The persons a letter is addressed to, each with initials; ids nobody knows are skipped. */
export function Addressees({ persons, ids }: { persons: Person[]; ids: string[] }) {
  const people = ids.flatMap((id) => {
    const person = persons.find((candidate) => candidate.id === id);
    return person ? [{ id: person.id, name: person.display_name }] : [];
  });
  return <Personas people={people} />;
}

/** A date-only value (`2026-11-05`) shown without a timezone shift. */
export function formatLetterDate(value: string): string {
  return date.format(new Date(`${value}T12:00:00`));
}

type Headline = Pick<
  DocumentSummary,
  | "sender"
  | "document_type"
  | "letter_date"
  | "proposed_sender"
  | "proposed_document_type"
  | "proposed_letter_date"
>;

/** A value the AI proposed and nobody confirmed yet: italic, and "(voorstel)" in words,
 * so it is never read as a fact (also not by a screen reader). */
function Proposed({ children }: { children: string }) {
  return (
    <span className="proposed">
      <em>{children}</em> <span className="muted">{t`(voorstel)`}</span>
    </span>
  );
}

/** The sender; before a review the AI's proposal, marked as one. */
export function SenderText({ document }: { document: Headline }) {
  if (document.sender) return <>{document.sender}</>;
  if (document.proposed_sender) return <Proposed>{document.proposed_sender}</Proposed>;
  return <>{t`Onbekende afzender`}</>;
}

/** The kind of letter; before a review the AI's proposal, marked as one. */
export function TypeText({ document }: { document: Headline }) {
  if (document.document_type) return <>{typeLabel(document.document_type)}</>;
  const proposed = typeLabel(document.proposed_document_type ?? "");
  return proposed ? <Proposed>{proposed}</Proposed> : null;
}

/** The letter's date; before a review the AI's proposal, marked as one. */
export function LetterDateText({ document }: { document: Headline }) {
  if (document.letter_date) return <>{formatLetterDate(document.letter_date)}</>;
  return document.proposed_letter_date ? (
    <Proposed>{formatLetterDate(document.proposed_letter_date)}</Proposed>
  ) : null;
}

const PHONE = "(max-width: 639px)";

/** "Ongelezen" (spec S 5.4): a dot in the brand colour, and the word for screen readers.
 * The caller's own state; the row is bold as well. */
function UnreadMark() {
  return (
    <>
      <span className="unread-dot" aria-hidden="true" />
      <span className="visually-hidden">{t`Ongelezen`}: </span>
    </>
  );
}

/**
 * The status badges of a row. With the letter status (`letterStatus`: every row, or only
 * reviewed ones) the badge "Nieuw", "Openstaand", ... and the marker "Te laat" come first;
 * the old word "Klaar" then goes (it said less), and the action words ("Controleren",
 * "Toewijzen") stay. Without it, or for an older server, the row is as before.
 */
export function RowStatus({
  document,
  departments,
  letterStatus,
}: {
  document: DocumentSummary;
  departments: boolean;
  letterStatus: "all" | "reviewed" | undefined;
}) {
  const reviewed = document.status === "done";
  const shown = Boolean(
    letterStatus && document.letter_status && (reviewed || letterStatus === "all"),
  );
  const replaced = shown && reviewed && document.inbox_state !== "unassigned";
  return (
    <>
      {shown ? (
        <LetterStatusBadge
          status={document.letter_status}
          overdue={document.overdue}
          waitingUntil={document.waiting_until}
        />
      ) : null}
      {replaced ? null : (
        <StatusBadge
          status={document.status}
          inboxState={document.inbox_state}
          departments={departments}
        />
      )}
    </>
  );
}

/** The post of one workspace: a row per document, the sender links to the document. */
export function DocumentTable<D extends DocumentSummary = DocumentSummary>({
  workspaceId,
  documents,
  persons,
  labels,
  empty,
  hint,
  fragment,
  caption,
  showUnread = false,
  letterStatus,
}: {
  workspaceId: string;
  documents: D[];
  persons: Person[];
  labels: LabelOut[];
  /** What to say when there are no letters. */
  empty: string;
  /** What to do about an empty list, in a sentence. */
  hint?: string;
  /** Something under the sender of a row, for example the part of the letter that was found. */
  fragment?: (document: D) => ReactNode;
  /** The name of the list for screen readers. */
  caption?: string;
  /** Marks the letters the caller has not read (Mijn post, Alle post; never the Inbox). */
  showUnread?: boolean;
  /** Shows the letter's status: on every row ("all"), or only on reviewed letters ("reviewed",
   * the Inbox: its rows keep their action words). */
  letterStatus?: "all" | "reviewed";
}) {
  const name = caption ?? t`Alle post`;
  const departments = useQuery(visibilityQuery(workspaceId)).data?.departments_enabled === true;
  // A phone shows a card per letter: seven columns do not fit, and a sideways scroll hides
  // the status. Tests (no media queries) and wider screens get the table.
  const phone = useMediaQuery(PHONE);
  if (documents.length === 0) {
    return (
      <section className="panel" aria-label={name}>
        <EmptyState icon="mail" hint={hint}>
          {empty}
        </EmptyState>
      </section>
    );
  }
  if (phone) {
    return (
      <section className="panel" aria-label={name}>
        <ul className="letter-cards">
          {documents.map((document) => (
            <li
              key={document.id}
              className={showUnread && document.unread ? "letter-card unread" : "letter-card"}
            >
              <div className="letter-card-top">
                {showUnread && document.unread ? <UnreadMark /> : null}
                <Link
                  to="/w/$workspaceId/post/$documentId"
                  params={{ workspaceId, documentId: document.id }}
                  className="letter-card-sender"
                >
                  <SenderText document={document} />
                </Link>
                <RowStatus
                  document={document}
                  departments={departments}
                  letterStatus={letterStatus}
                />
              </div>
              {fragment?.(document)}
              <p className="letter-card-meta">
                <span>
                  <TypeText document={document} />
                </span>
                <span className="num">
                  <LetterDateText document={document} />
                </span>
              </p>
              <Addressees persons={persons} ids={document.addressee_ids ?? []} />
              <span className="cell-badges">
                <LabelChips labels={labels} ids={document.label_ids ?? []} />
                <PrivateLock
                  visibility={document.visibility}
                  readerNames={document.reader_names}
                  variant="row"
                />
                <SuggestionMark count={document.open_suggestion_count} />
                <AutomaticBadge document={document} />
              </span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
    <section className="panel table-scroll" aria-label={name} tabIndex={0}>
      <table className="table table-wide">
        <thead>
          <tr>
            <th scope="col">{t`Afzender`}</th>
            <th scope="col">{t`Soort`}</th>
            <th scope="col">{t`Datum brief`}</th>
            <th scope="col">{t`Op naam van`}</th>
            <th scope="col">{t`Labels`}</th>
            <th scope="col">{t`Status`}</th>
            <th scope="col">{t`Ontvangen`}</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((document) => (
            <tr key={document.id} className={showUnread && document.unread ? "unread" : undefined}>
              <th scope="row" className="cell-strong">
                {showUnread && document.unread ? <UnreadMark /> : null}
                <Link
                  to="/w/$workspaceId/post/$documentId"
                  params={{ workspaceId, documentId: document.id }}
                >
                  <SenderText document={document} />
                </Link>
                {fragment?.(document)}
              </th>
              <td>
                <TypeText document={document} />
              </td>
              <td className="num">
                <LetterDateText document={document} />
              </td>
              <td>
                <Addressees persons={persons} ids={document.addressee_ids ?? []} />
              </td>
              <td>
                <LabelChips labels={labels} ids={document.label_ids ?? []} />
              </td>
              <td>
                <span className="cell-badges">
                  <RowStatus
                    document={document}
                    departments={departments}
                    letterStatus={letterStatus}
                  />
                  <PrivateLock
                    visibility={document.visibility}
                    readerNames={document.reader_names}
                    variant="row"
                  />
                  <SuggestionMark count={document.open_suggestion_count} />
                  <AutomaticBadge document={document} />
                </span>
              </td>
              <td className="num">
                <time dateTime={document.created_at}>
                  {date.format(new Date(document.created_at))}
                </time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
