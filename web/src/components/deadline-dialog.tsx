import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button, Dialog, Form, Heading, Modal, ModalOverlay } from "react-aria-components";
import { refreshAfterTask, setTaskDeadline } from "../api/tasks";
import { ErrorAlert } from "./page";

/**
 * "Datum invullen": the letter names a term ("binnen zes weken na dagtekening"); the
 * reader fills in the date it comes to. A plain date field, as the search filters use.
 */
export function DeadlineDialog({
  workspaceId,
  taskId,
  description,
  term,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  taskId: string;
  description: string;
  term: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const fieldId = useId();
  const [day, setDay] = useState("");
  const save = useMutation({
    mutationFn: () => setTaskDeadline(workspaceId, taskId, day),
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
            {t`Datum invullen`}
          </Heading>
          <p className="muted">{description}</p>
          {term ? <p>{t`De brief noemt een termijn: ‘${term}’.`}</p> : null}
          <Form
            className="form"
            onSubmit={(event) => {
              event.preventDefault();
              if (day) save.mutate();
            }}
          >
            <div className="field">
              <label htmlFor={fieldId}>{t`Welke datum noemt de brief?`}</label>
              <input
                id={fieldId}
                type="date"
                required
                value={day}
                onChange={(event) => setDay(event.target.value)}
              />
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
