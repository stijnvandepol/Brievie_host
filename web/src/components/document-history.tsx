import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
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
  const label = event.label_name;
  switch (event.kind) {
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
    case "task_completed":
      return t`${who} rondde een taak af`;
    case "task_reopened":
      return t`${who} zette een taak weer open`;
    default:
      return null;
  }
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

/** What happened to a letter, newest first; a member can undo what a rule did. */
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
              {event.undone ? <span className="muted small">{t`Teruggedraaid`}</span> : null}
              {canEdit && event.can_undo ? (
                <Button
                  className="button-text"
                  aria-label={t`Terugdraaien: ${text}`}
                  isDisabled={undo.isPending}
                  onPress={() => undo.mutate(event.id)}
                >
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
