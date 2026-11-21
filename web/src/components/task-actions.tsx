import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "react-aria-components";
import { meQuery } from "../api/queries";
import { assignTask, refreshAfterTask } from "../api/tasks";
import { AssignDialog } from "./assign-dialog";
import { DeadlineDialog } from "./deadline-dialog";
import { ErrorAlert } from "./page";

export type ActionFacts = {
  id: string;
  description: string;
  document_id?: string | null;
  deadline: string | null;
  term_quote?: string;
  assigned_to_id?: string | null;
};

/**
 * What a member can do with a task besides ticking it: "Ik doe dit", "Niet meer aan mij",
 * "Geef aan…" and, when the letter names a term but no date is known, "Datum invullen".
 * Only for a task on a letter (one without a letter is not given to anyone, S 5.3).
 */
export function TaskActions({
  workspaceId,
  task,
  showTake = true,
}: {
  workspaceId: string;
  task: ActionFacts;
  /** "Ik doe dit" here; "Op te pakken" shows its own. */
  showTake?: boolean;
}) {
  const queryClient = useQueryClient();
  const me = useQuery(meQuery).data;
  const [dialog, setDialog] = useState<"assign" | "deadline" | null>(null);
  const assign = useMutation({
    mutationFn: (userId: string | null) => assignTask(workspaceId, task.id, userId),
    onSettled: () => refreshAfterTask(queryClient, workspaceId),
  });
  const description = task.description;
  const mine = me !== undefined && me !== null && task.assigned_to_id === me.id;
  const term = task.term_quote ?? "";
  const needsDate = task.deadline === null && term !== "";
  return (
    <>
      {needsDate ? (
        <Button
          className="button-text"
          aria-label={t`Datum invullen: ${description}`}
          onPress={() => setDialog("deadline")}
        >
          {t`Datum invullen`}
        </Button>
      ) : null}
      {task.document_id ? (
        <>
          {mine ? (
            <Button
              className="button-text"
              aria-label={t`Niet meer aan mij: ${description}`}
              isDisabled={assign.isPending}
              onPress={() => assign.mutate(null)}
            >
              {t`Niet meer aan mij`}
            </Button>
          ) : showTake && me ? (
            <Button
              className="button-text"
              aria-label={t`Ik doe dit: ${description}`}
              isDisabled={assign.isPending}
              onPress={() => assign.mutate(me.id)}
            >
              {t`Ik doe dit`}
            </Button>
          ) : null}
          <Button
            className="button-text"
            aria-label={t`Geef aan: ${description}`}
            onPress={() => setDialog("assign")}
          >
            {t`Geef aan…`}
          </Button>
        </>
      ) : null}
      <ErrorAlert error={assign.error} />
      <AssignDialog
        workspaceId={workspaceId}
        taskId={task.id}
        description={description}
        current={task.assigned_to_id ?? null}
        isOpen={dialog === "assign"}
        onOpenChange={(open) => setDialog(open ? "assign" : null)}
      />
      <DeadlineDialog
        workspaceId={workspaceId}
        taskId={task.id}
        description={description}
        term={term}
        isOpen={dialog === "deadline"}
        onOpenChange={(open) => setDialog(open ? "deadline" : null)}
      />
    </>
  );
}
