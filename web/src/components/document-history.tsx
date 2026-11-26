import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { documentEventsQuery, meQuery } from "../api/queries";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { formatLetterDate } from "./document-table";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";

type DocumentEvent = components["schemas"]["DocumentEventOut"];

const dateTime = dateFormat({ dateStyle: "medium", timeStyle: "short" });

/** One event as a sentence, or `null` for a kind this version does not know yet. */
function sentence(
  event: DocumentEvent,
  me: { id: string; name: string } | null | undefined,
  movedAway: boolean,
): string | null {
  const ruleName = event.rule_name;
  const who =
    event.origin === "rule" && ruleName
      ? t`Regel ${ruleName}`
      : isMine(event, me)
        ? t`Jij`
        : event.actor_name || t`Iemand`;
  const person = event.person_name;
  const label = event.label_name;
  // What Brievie did itself has no person behind it.
  if (event.origin === "rule_autopilot" && event.kind === "label_added") {
    return ruleName
      ? t`Automatisch toegevoegd door regel ${ruleName}: label ${label}`
      : t`Automatisch toegevoegd door een regel: label ${label}`;
  }
  // A letter from a person's or department's folder of the scan folder (intake S 3.2a).
  if (event.origin === "watch_folder" && event.kind === "addressee_added") {
    return t`Automatisch toegevoegd uit de scanmap van ${person}`;
  }
  if (event.origin === "ai" || event.origin === "name_match") {
    if (event.kind === "label_added") {
      return event.origin === "ai"
        ? t`Automatisch door AI toegevoegd: label ${label}`
        : t`Automatisch toegevoegd: label ${label}`;
    }
    if (event.kind === "addressee_added") {
      return event.origin === "ai"
        ? t`Automatisch door AI toegevoegd: ${person}`
        : t`Automatisch toegevoegd: naam herkend (${person})`;
    }
    if (event.kind === "confirmed" && event.origin === "ai") {
      return t`Automatisch bevestigd door AI`;
    }
  }
  // The person may no longer read the letter (or became a viewer): Brievie took the task back.
  if (event.origin === "system" && event.kind === "task_unassigned") {
    return t`Brievie haalde een taak weg bij ${person}: die mag deze brief niet meer lezen`;
  }
  // A merge of two scans of one letter, or the choice that they are two letters.
  const by = isMine(event, me) ? t`jou` : event.actor_name || t`iemand`;
  if (event.origin === "merge") {
    switch (event.kind) {
      case "trashed":
        return t`Als dubbele brief naar de prullenbak verplaatst door ${by}`;
      case "task_moved":
        return movedAway
          ? t`Taak verplaatst naar de dubbele brief`
          : t`Taak verplaatst uit de dubbele brief`;
      case "label_added":
        return t`${who} zette het label ${label} erop (uit de dubbele brief)`;
      case "addressee_added":
        return t`${who} zette deze brief op naam van ${person} (uit de dubbele brief)`;
      case "task_completed":
        return t`${who} rondde een taak af (uit de dubbele brief)`;
    }
  }
  switch (event.kind) {
    case "merged":
      return t`Samengevoegd met een dubbele brief door ${by}`;
    case "duplicate_dismissed":
      return t`Gekozen: geen dubbele brief, door ${by}`;
    case "label_added":
      return t`${who} zette het label ${label} erop`;
    case "label_removed":
      return t`${who} haalde het label ${label} eraf`;
    case "addressee_added":
      return t`${who} zette deze brief op naam van ${person}`;
    case "addressee_removed":
      return t`${who} haalde ${person} weg`;
    case "confirmed":
      return t`${who} bevestigde deze brief`;
    case "reopened":
      return t`${who} zette de brief terug in de Inbox om te controleren`;
    case "task_completed":
      return t`${who} rondde een taak af`;
    case "task_reopened":
      return t`${who} zette een taak weer open`;
    case "task_assigned":
      return t`${who} gaf een taak aan ${person}`;
    case "task_unassigned":
      return t`${who} haalde een taak weg bij ${person}`;
    case "task_deadline_set":
      return t`${who} vulde de datum van een taak in`;
    case "trashed": {
      // Without anybody behind it, Brievie did it.
      const trasher = isMine(event, me) ? t`jou` : event.actor_name || t`Brievie`;
      return t`Naar de prullenbak verplaatst door ${trasher}`;
    }
    case "restored":
      return t`Teruggezet uit de prullenbak door ${by}`;
    // Who reads the letter (private letters, sharing, grants, emergency access).
    case "made_private":
      return t`${who} maakte deze brief privé`;
    case "made_public":
      return t`${who} maakte deze brief weer zichtbaar: hij is niet meer privé`;
    case "share_added":
      return t`${who} deelde deze brief met ${person}`;
    case "share_removed":
      return t`${who} haalde ${person} weg bij delen`;
    case "access_granted": {
      const grantee = event.note ?? "";
      return t`${grantee} kan deze brief nu lezen (gekoppeld aan ${person})`;
    }
    case "emergency_opened": {
      const reason = event.note ?? "";
      return t`${who} opende deze brief met noodtoegang: ${reason}`;
    }
    case "private_skipped":
      return event.note
        ? event.note
        : t`Deze brief kon niet privé worden: dan zou niemand hem kunnen lezen. Hij is zichtbaar gebleven.`;
    // Own documents (archive): added, a detail changed (the field's name; a date, never the
    // title's words), "Bewaren tot", and the reminder Brievie made itself.
    case "document_added":
      return t`${who} voegde dit document toe aan het archief`;
    case "details_changed":
      return detailSentence(who, event.note ?? "", day(event));
    case "retention_changed": {
      const until = day(event);
      return until ? t`${who} zette Bewaren tot op ${until}` : t`${who} haalde Bewaren tot weg`;
    }
    case "reminder_started": {
      const until = day(event);
      return t`Brievie zette een herinnering in Mijn taken: geldig tot ${until}`;
    }
    default:
      return null;
  }
}

/** The day an event names, as "12 mrt 2031"; empty when it names none. */
function day(event: DocumentEvent): string {
  return event.on_date ? formatLetterDate(event.on_date) : "";
}

/** One changed detail of an own document, by the field's name. */
function detailSentence(who: string, field: string, date: string): string {
  switch (field) {
    case "title":
      return t`${who} paste de titel aan`;
    case "document_type":
      return t`${who} paste het type aan`;
    case "document_date":
      return t`${who} zette de datum op ${date}`;
    case "expires_on":
      return date ? t`${who} zette Geldig tot op ${date}` : t`${who} haalde Geldig tot weg`;
    case "remind_days":
      return t`${who} paste de herinnering aan`;
    default:
      return t`${who} paste de gegevens aan`;
  }
}

/** By the actor's id; by the name only for an older server that sends no id. */
function isMine(
  event: DocumentEvent,
  me: { id: string; name: string } | null | undefined,
): boolean {
  if (!me) return false;
  if (event.actor_id !== undefined) return event.actor_id === me.id;
  return event.actor_name !== "" && event.actor_name === me.name;
}

/** Reverses a change a rule made; the answer is the document as it is now. */
function useUndoEvent(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (eventId: string) => {
      const { data, error } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/events/{event_id}/undo",
        {
          params: {
            path: { workspace_id: workspaceId, document_id: documentId, event_id: eventId },
          },
        },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (document) => {
      queryClient.setQueryData(["workspaces", workspaceId, "documents", documentId], document);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "inbox-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "persons"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "labels"] }),
      ]);
    },
  });
}

/** What happened to a letter, newest first; a member can undo what a rule or the AI did. */
export function DocumentHistory({
  workspaceId,
  documentId,
  canEdit,
}: {
  workspaceId: string;
  documentId: string;
  canEdit: boolean;
}) {
  const { data, error } = useQuery(documentEventsQuery(workspaceId, documentId));
  const { data: me } = useQuery(meQuery);
  const undo = useUndoEvent(workspaceId, documentId);
  // The letters this one was merged into (it went to the trash): a task moved with one of
  // them went from here. Without the other letter's id (purged, or not readable) a task
  // moved away when this letter ever went to the trash in a merge.
  const trashedMerges = (data ?? []).filter(
    (event) => event.kind === "trashed" && event.origin === "merge",
  );
  const keptLetters = new Set(trashedMerges.map((event) => event.related_document_id));
  const movedAway = (event: DocumentEvent) =>
    event.related_document_id
      ? keptLetters.has(event.related_document_id)
      : trashedMerges.length > 0;
  const lines = (data ?? []).flatMap((event) => {
    const text = sentence(event, me, movedAway(event));
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
              <span className="history-text">{text}</span>
              <span className="history-meta">
                <time dateTime={event.created_at}>
                  {dateTime.format(new Date(event.created_at))}
                </time>
                {event.undone ? <span>{t`Teruggedraaid`}</span> : null}
              </span>
              {canEdit && event.can_undo ? (
                <Button
                  className="button-text"
                  aria-label={t`Terugdraaien: ${text}`}
                  isDisabled={undo.isPending}
                  onPress={() => undo.mutate(event.id)}
                >
                  <Icon name="undo" />
                  {t`Terugdraaien`}
                </Button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      <ErrorAlert error={undo.error} />
    </section>
  );
}
