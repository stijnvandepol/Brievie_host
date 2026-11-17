import { t } from "@lingui/core/macro";
import { Link } from "@tanstack/react-router";
import type { components } from "../api/schema";
import { LabelChips } from "./label-chip";
import { SuggestionMark } from "./suggestion-list";
import { Badge, EmptyState, type Intent, Personas, useMediaQuery } from "./ui";

type DocumentSummary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];
type LabelOut = components["schemas"]["LabelOut"];

const date = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium" });

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
      return t`Wordt verwerkt`;
    case "needs_review":
      return t`Te controleren`;
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

/** The status of a letter as a badge: its word, a tint and an icon. */
export function StatusBadge({ status }: { status: string }) {
  return <Badge intent={statusIntent(status)}>{statusLabel(status)}</Badge>;
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

const PHONE = "(max-width: 639px)";

/** The post of one workspace: a row per document, the sender links to the document. */
export function DocumentTable({
  workspaceId,
  documents,
  persons,
  labels,
  empty,
  hint,
}: {
  workspaceId: string;
  documents: DocumentSummary[];
  persons: Person[];
  labels: LabelOut[];
  /** What to say when there are no letters. */
  empty: string;
  /** What to do about an empty list, in a sentence. */
  hint?: string;
}) {
  // A phone shows a card per letter: seven columns do not fit, and a sideways scroll hides
  // the status. Tests (no media queries) and wider screens get the table.
  const phone = useMediaQuery(PHONE);
  if (documents.length === 0) {
    return (
      <section className="panel" aria-label={t`Post`}>
        <EmptyState icon="mail" hint={hint}>
          {empty}
        </EmptyState>
      </section>
    );
  }
  if (phone) {
    return (
      <section className="panel" aria-label={t`Post`}>
        <ul className="letter-cards">
          {documents.map((document) => (
            <li key={document.id} className="letter-card">
              <div className="letter-card-top">
                <Link
                  to="/w/$workspaceId/post/$documentId"
                  params={{ workspaceId, documentId: document.id }}
                  className="letter-card-sender"
                >
                  {document.sender || t`Onbekende afzender`}
                </Link>
                <StatusBadge status={document.status} />
              </div>
              <p className="letter-card-meta">
                <span>{typeLabel(document.document_type)}</span>
                {document.letter_date ? (
                  <span className="num">{formatLetterDate(document.letter_date)}</span>
                ) : null}
              </p>
              <Addressees persons={persons} ids={document.addressee_ids ?? []} />
              <span className="cell-badges">
                <LabelChips labels={labels} ids={document.label_ids ?? []} />
                <SuggestionMark count={document.open_suggestion_count} />
              </span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
    <section className="panel table-scroll" aria-label={t`Post`} tabIndex={0}>
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
            <tr key={document.id}>
              <th scope="row" className="cell-strong">
                <Link
                  to="/w/$workspaceId/post/$documentId"
                  params={{ workspaceId, documentId: document.id }}
                >
                  {document.sender || t`Onbekende afzender`}
                </Link>
              </th>
              <td>{typeLabel(document.document_type)}</td>
              <td className="num">
                {document.letter_date ? formatLetterDate(document.letter_date) : ""}
              </td>
              <td>
                <Addressees persons={persons} ids={document.addressee_ids ?? []} />
              </td>
              <td>
                <LabelChips labels={labels} ids={document.label_ids ?? []} />
              </td>
              <td>
                <span className="cell-badges">
                  <StatusBadge status={document.status} />
                  <SuggestionMark count={document.open_suggestion_count} />
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
