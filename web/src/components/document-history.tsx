import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { documentEventsQuery, meQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert } from "./page";

type DocumentEvent = components["schemas"]["DocumentEventOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium", timeStyle: "short" });

/** One event as a sentence, or `null` for a kind this version does not know yet. */
function sentence(event: DocumentEvent, myName: string | undefined): string | null {
  const ruleName = event.rule_name;
  const who =
    event.origin === "rule" && ruleName
      ? t`Regel ${ruleName}`
      : event.actor_name && event.actor_name === myName
        ? t`Jij`
        : event.actor_name || t`Iemand`;
  const person = event.person_name;
  switch (event.kind) {
    case "addressee_added":
      return t`${who} zette deze brief op naam van ${person}`;
    case "addressee_removed":
      return t`${who} haalde ${person} weg`;
    case "confirmed":
      return t`${who} bevestigde deze brief`;
    default:
      return null;
  }
}

/** What happened to a letter, newest first. */
export function DocumentHistory({
  workspaceId,
  documentId,
}: {
  workspaceId: string;
  documentId: string;
}) {
  const { data, error } = useQuery(documentEventsQuery(workspaceId, documentId));
  const { data: me } = useQuery(meQuery);
  const lines = (data ?? []).flatMap((event) => {
    const text = sentence(event, me?.name);
    return text === null ? [] : [{ event, text }];
  });
  return (
    <section className="panel" aria-labelledby="history-heading">
      <div className="panel-head">
        <h2 id="history-heading">{t`Geschiedenis`}</h2>
      </div>
      <ErrorAlert error={error} />
      {data && lines.length === 0 ? (
        <p className="panel-note">{t`Er is nog niets gebeurd.`}</p>
      ) : null}
      {lines.length > 0 ? (
        <ol className="history-list">
          {lines.map(({ event, text }) => (
            <li key={event.id}>
              <span>{text}</span>
              <time className="muted small" dateTime={event.created_at}>
                {dateTime.format(new Date(event.created_at))}
              </time>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
