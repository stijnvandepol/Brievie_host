import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button, Dialog, Form, Heading, Modal, ModalOverlay } from "react-aria-components";
import { refreshAfterTask, snoozeTask } from "../api/tasks";
import { addDays, amsterdamToday } from "../task-time";
import { ErrorAlert } from "./page";

/**
 * "Uitstellen…": the task leaves the member's own list until a day. Only they see it. It
 * cannot go past the task's own date, so a term is never hidden (the field stops there).
 */
export function SnoozeDialog({
  workspaceId,
  taskId,
  description,
  deadline,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  taskId: string;
  description: string;
  deadline: string | null;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const fieldId = useId();
  const [day, setDay] = useState("");
  const today = amsterdamToday();
  const year = addDays(today, 365);
  const max = deadline !== null && deadline < year ? deadline : year;
  const save = useMutation({
    mutationFn: () => snoozeTask(workspaceId, taskId, day),
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
            {t`Uitstellen…`}
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
              <label htmlFor={fieldId}>{t`Tot wanneer stel je dit uit?`}</label>
              <input
                id={fieldId}
                type="date"
                required
                min={addDays(today, 1)}
                max={max}
                value={day}
                onChange={(event) => setDay(event.target.value)}
              />
              <p className="muted small">
                {t`Alleen jij ziet dat je dit uitstelt. Op die dag staat de taak weer in je lijst. Uitstellen kan tot de datum van de taak.`}
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
