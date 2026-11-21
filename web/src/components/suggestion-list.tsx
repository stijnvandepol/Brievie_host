import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";
import { Badge } from "./ui";

type Document = components["schemas"]["DocumentOut"];
type Suggestion = components["schemas"]["SuggestionOut"];
type Person = components["schemas"]["PersonOut"];
type Label = components["schemas"]["LabelOut"];

/** The proposed change as a question, or `null` when its person or label is unknown here. */
function question(suggestion: Suggestion, persons: Person[], labels: Label[]): string | null {
  if (suggestion.action === "add_addressee") {
    const name = persons.find((person) => person.id === suggestion.person_id)?.display_name;
    return name === undefined ? null : t`Op naam van ${name}?`;
  }
  if (suggestion.action === "add_label") {
    const name = labels.find((label) => label.id === suggestion.label_id)?.name;
    return name === undefined ? null : t`Label ${name}?`;
  }
  return null;
}

function originText(suggestion: Suggestion): string {
  const ruleName = suggestion.rule_name;
  switch (suggestion.origin) {
    case "name_match":
      return t`Herkend in de brief`;
    case "ai":
      return t`Voorstel van AI`;
    case "rule":
      return t`Regel: ${ruleName}`;
    default:
      return "";
  }
}

/** Accepts or dismisses one suggestion; the answer is the document as it is now. */
function useAnswerSuggestion(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, verb }: { id: string; verb: "accept" | "dismiss" }) => {
      const params = {
        path: { workspace_id: workspaceId, document_id: documentId, suggestion_id: id },
      };
      const { data, error } =
        verb === "accept"
          ? await api.POST(
              "/api/v1/workspaces/{workspace_id}/documents/{document_id}/suggestions/{suggestion_id}/accept",
              { params },
            )
          : await api.POST(
              "/api/v1/workspaces/{workspace_id}/documents/{document_id}/suggestions/{suggestion_id}/dismiss",
              { params },
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

/** The open suggestions of a letter, each with "Toevoegen" and "Negeren" for a member. */
export function SuggestionList({
  workspaceId,
  document,
  persons,
  labels,
  canEdit,
}: {
  workspaceId: string;
  document: Document;
  persons: Person[];
  labels: Label[];
  canEdit: boolean;
}) {
  const answer = useAnswerSuggestion(workspaceId, document.id);
  const items = (document.suggestions ?? []).flatMap((suggestion) => {
    const text = question(suggestion, persons, labels);
    return text === null ? [] : [{ suggestion, text }];
  });
  if (items.length === 0) return null;
  const disabled = answer.isPending || document.status === "processing";
  return (
    <section className="panel" aria-labelledby="suggestions-heading">
      <div className="panel-head">
        <h2 id="suggestions-heading">{t`Voorstellen`}</h2>
      </div>
      <ul className="suggestion-list">
        {items.map(({ suggestion, text }) => (
          <li key={suggestion.id} className="suggestion">
            <div>
              <p className="suggestion-question">{text}</p>
              <p className="muted small">{originText(suggestion)}</p>
              {suggestion.quote ? <q className="muted small block">{suggestion.quote}</q> : null}
            </div>
            {canEdit ? (
              <div className="suggestion-actions">
                <Button
                  className="button small"
                  aria-label={t`Toevoegen: ${text}`}
                  isDisabled={disabled}
                  onPress={() => answer.mutate({ id: suggestion.id, verb: "accept" })}
                >
                  <Icon name="checkmark" />
                  {t`Toevoegen`}
                </Button>
                <Button
                  className="button secondary small"
                  aria-label={t`Negeren: ${text}`}
                  isDisabled={disabled}
                  onPress={() => answer.mutate({ id: suggestion.id, verb: "dismiss" })}
                >
                  <Icon name="dismiss" />
                  {t`Negeren`}
                </Button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      <ErrorAlert error={answer.error} />
    </section>
  );
}

/** The mark in a list for a letter with open suggestions. */
export function SuggestionMark({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <Badge intent="brand" icon="lightbulb">
      {t`voorstel`}
    </Badge>
  );
}

/**
 * The AI's proposals that the letter is for somebody nobody knows yet (Mensen, B3): the
 * name, the words that led to it, "Toevoegen als persoon" (the person is made and the
 * letter put on them) and "Negeren" (never asked again on this letter). Nothing makes the
 * person on its own.
 */
export function NewPersonList({
  workspaceId,
  document,
  canEdit,
}: {
  workspaceId: string;
  document: Document;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const answer = useMutation({
    mutationFn: async ({ id, verb }: { id: string; verb: "accept" | "dismiss" }) => {
      const params = {
        path: { workspace_id: workspaceId, document_id: document.id, suggestion_id: id },
      };
      const { data, error } =
        verb === "accept"
          ? await api.POST(
              "/api/v1/workspaces/{workspace_id}/documents/{document_id}/new-person-suggestions/{suggestion_id}/accept",
              { params },
            )
          : await api.POST(
              "/api/v1/workspaces/{workspace_id}/documents/{document_id}/new-person-suggestions/{suggestion_id}/dismiss",
              { params },
            );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (answered) => {
      queryClient.setQueryData(["workspaces", workspaceId, "documents", document.id], answered);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "persons"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "people"] }),
      ]);
    },
  });
  const items = document.new_person_suggestions ?? [];
  if (items.length === 0) return null;
  const disabled = answer.isPending || document.status === "processing";
  return (
    <section className="panel" aria-labelledby="new-person-heading">
      <div className="panel-head">
        <h2 id="new-person-heading">{t`Nieuwe persoon?`}</h2>
      </div>
      <ul className="suggestion-list">
        {items.map((item) => {
          const name = item.name;
          return (
            <li key={item.id} className="suggestion">
              <div>
                <p className="suggestion-question">
                  {t`Deze brief is voor ${name}. Die persoon staat nog niet in Brievie.`}
                </p>
                <p className="muted small">{t`Voorstel van AI`}</p>
                {item.quote ? <q className="muted small block">{item.quote}</q> : null}
              </div>
              {canEdit ? (
                <div className="suggestion-actions">
                  <Button
                    className="button small"
                    aria-label={t`Toevoegen als persoon: ${name}`}
                    isDisabled={disabled}
                    onPress={() => answer.mutate({ id: item.id, verb: "accept" })}
                  >
                    <Icon name="personAdd" />
                    {t`Toevoegen als persoon`}
                  </Button>
                  <Button
                    className="button secondary small"
                    aria-label={t`Negeren: ${name}`}
                    isDisabled={disabled}
                    onPress={() => answer.mutate({ id: item.id, verb: "dismiss" })}
                  >
                    <Icon name="dismiss" />
                    {t`Negeren`}
                  </Button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      <ErrorAlert error={answer.error} />
    </section>
  );
}
