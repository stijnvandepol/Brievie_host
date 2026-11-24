import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button, Dialog, Form, Heading, Modal, ModalOverlay } from "react-aria-components";
import { refreshAfterTask, setTaskWaiting } from "../api/tasks";
import { addDays, amsterdamToday } from "../task-time";
import { ErrorAlert } from "./page";

/**
 * "Wacht op reactie…": until when the other party has the next move. A plain date field
 * (as "Datum invullen"): after today, within a year. Everyone who sees the letter sees it.
 */
export function WaitingDialog({
  workspaceId,
  taskId,
  description,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  taskId: string;
  description: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const fieldId = useId();
  const [day, setDay] = useState("");
  const today = amsterdamToday();
  const save = useMutation({
    mutationFn: () => setTaskWaiting(workspaceId, taskId, day),
    onSuccess: async () => {
      await refreshAfterTask(queryClient, workspaceId);
      onOpenChange(false);
    },
  });
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="overlay">
      <Modal className="modal">
        <Dialog className="dialog">
          <Heading slot="title" className="dialog-title">
            {t`Wacht op reactie…`}
          </Heading>
          <p className="muted">{description}</p>
          <Form
            className="form"
            onSubmit={(event) => {
              event.preventDefault();
              if (day) save.mutate();
            }}
          >
            <div className="field">
              <label htmlFor={fieldId}>{t`Tot wanneer wacht je op een reactie?`}</label>
              <input
                id={fieldId}
                type="date"
                required
                min={addDays(today, 1)}
                max={addDays(today, 365)}
                value={day}
                onChange={(event) => setDay(event.target.value)}
              />
              <p className="muted small">
                {t`Op die dag komt de taak terug in Mijn taken. Iedereen die deze brief ziet, ziet dat je wacht.`}
              </p>
            </div>
            <ErrorAlert error={save.error} />
            <div className="dialog-actions">
              <Button className="button secondary" onPress={() => onOpenChange(false)}>
                {t`Annuleren`}
              </Button>
              <Button type="submit" className="button" isPending={save.isPending}>
                {t`Opslaan`}
              </Button>
            </div>
          </Form>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
