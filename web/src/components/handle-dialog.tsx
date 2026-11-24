import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";
import type { components } from "../api/schema";
import { handleDocument, refreshAfterTask } from "../api/tasks";
import { amountText } from "../task-time";
import { formatLetterDate } from "./document-table";
import { ErrorAlert } from "./page";

type Task = components["schemas"]["TaskOut"];

/**
 * "Brief afhandelen?": names the open tasks that will be ticked off, and for a payment asks
 * for a tick first. The server checks both: when a task was added or ticked meanwhile it
 * answers `tasks_changed` and nothing is ticked off; the letter is read again and the list
 * here follows it.
 */
export function HandleDialog({
  workspaceId,
  documentId,
  tasks,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  documentId: string;
  tasks: Task[];
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const boxId = useId();
  const open = tasks.filter((task) => task.status === "open");
  const hasPayment = open.some((task) => task.type === "pay");
  const [confirmed, setConfirmed] = useState(false);
  const run = useMutation({
    mutationFn: () =>
      handleDocument(
        workspaceId,
        documentId,
        open.map((task) => task.id),
        confirmed,
      ),
    onSuccess: async () => {
      await refreshAfterTask(queryClient, workspaceId);
      onOpenChange(false);
    },
    // The tasks changed or the letter did: read it again, the list follows it.
    onError: () => refreshAfterTask(queryClient, workspaceId),
  });
  const { reset } = run;
  // A tick belongs to the payments it was given for: when the open tasks change (a refresh
  // after `tasks_changed`), it goes, so a new payment is never confirmed unseen.
  const openKey = open.map((task) => task.id).join(",");
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key is the trigger
  useEffect(() => {
    setConfirmed(false);
  }, [openKey]);
  useEffect(() => {
    if (!isOpen) {
      reset();
      setConfirmed(false);
    }
  }, [isOpen, reset]);
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="overlay">
      <Modal className="modal">
        <Dialog role="alertdialog" className="dialog">
          <Heading slot="title" className="dialog-title">
            {t`Brief afhandelen?`}
          </Heading>
          <p>{t`Deze taken worden afgevinkt:`}</p>
          <ul className="handle-tasks">
            {open.map((task) => {
              const amount = amountText(task.amount ?? "");
              const when = task.deadline ? formatLetterDate(task.deadline) : "";
              return (
                <li key={task.id}>
                  {task.description}
                  {when || amount ? (
                    <span className="muted small">
                      {" "}
                      ({[when, amount].filter(Boolean).join(", ")})
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {hasPayment ? (
            <div className="check">
              <input
                id={boxId}
                type="checkbox"
                required
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <label htmlFor={boxId}>{t`De betaling is gedaan of geregeld`}</label>
            </div>
          ) : null}
          <ErrorAlert error={run.error} />
          <div className="dialog-actions">
            <Button autoFocus className="button secondary" onPress={() => onOpenChange(false)}>
              {t`Annuleren`}
            </Button>
            <Button
              className="button"
              isPending={run.isPending}
              isDisabled={open.length === 0 || (hasPayment && !confirmed)}
              onPress={() => run.mutate()}
            >
              {t`Afhandelen`}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
