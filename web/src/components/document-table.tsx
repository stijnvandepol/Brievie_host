import { t } from "@lingui/core/macro";
import { Link } from "@tanstack/react-router";
import type { components } from "../api/schema";
import { LabelChips } from "./label-chip";
import { personNames } from "./person-picker";
import { SuggestionMark } from "./suggestion-list";

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

/** A date-only value (`2026-11-05`) shown without a timezone shift. */
export function formatLetterDate(value: string): string {
  return date.format(new Date(`${value}T12:00:00`));
}

/** The post of one workspace: a row per document, the sender links to the document. */
export function DocumentTable({
  workspaceId,
  documents,
  persons,
  labels,
  empty,
}: {
  workspaceId: string;
  documents: DocumentSummary[];
  persons: Person[];
  labels: LabelOut[];
  /** What to say when there are no letters. */
  empty: string;
}) {
  if (documents.length === 0) {
    return (
      <section className="panel" aria-label={t`Post`}>
        <p className="panel-note">{empty}</p>
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
              <td>{personNames(persons, document.addressee_ids).join(", ")}</td>
              <td>
                <LabelChips labels={labels} ids={document.label_ids} />
              </td>
              <td>
                <span className="tag">{statusLabel(document.status)}</span>{" "}
                <SuggestionMark count={document.open_suggestion_count} />
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
