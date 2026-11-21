import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Button,
  Dialog,
  Form,
  Heading,
  Label,
  Modal,
  ModalOverlay,
  Radio,
  RadioGroup,
  Text,
} from "react-aria-components";
import { assignTask, refreshAfterTask, taskAssigneesQuery } from "../api/tasks";
import { ErrorAlert } from "./page";
import { Loading } from "./ui";

function AssignForm({
  workspaceId,
  taskId,
  current,
  onClose,
}: {
  workspaceId: string;
  taskId: string;
  current: string | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const people = useQuery(taskAssigneesQuery(workspaceId, taskId));
  const [chosen, setChosen] = useState<string>(current ?? "");
  const save = useMutation({
    mutationFn: () => assignTask(workspaceId, taskId, chosen || null),
    onSuccess: async () => {
      await refreshAfterTask(queryClient, workspaceId);
      onClose();
    },
  });
  return (
    <Form
      className="form"
      onSubmit={(event) => {
        event.preventDefault();
        if (chosen) save.mutate();
      }}
    >
      <ErrorAlert error={people.error} />
      {people.isPending ? <Loading /> : null}
      {people.data ? (
        <RadioGroup className="choice" value={chosen} onChange={setChosen} isRequired>
          <Label className="choice-label">{t`Aan wie geef je deze taak?`}</Label>
          <Text slot="description" className="muted small">
            {t`Alleen wie deze brief mag lezen, staat in de lijst.`}
          </Text>
          <div className="cards cards-stack">
            {people.data.map((person) => (
              <Radio key={person.user_id} value={person.user_id} className="card">
                <span className="card-top">
                  <strong>{person.is_me ? t`${person.name} (jij)` : person.name}</strong>
                  <span className="radio-indicator" aria-hidden="true" />
                </span>
              </Radio>
            ))}
          </div>
        </RadioGroup>
      ) : null}
      <ErrorAlert error={save.error} />
      <div className="dialog-actions">
        <Button className="button secondary" onPress={onClose}>
          {t`Annuleren`}
        </Button>
        <Button type="submit" className="button" isPending={save.isPending} isDisabled={!chosen}>
          {t`Geef aan`}
        </Button>
      </div>
    </Form>
  );
}

/** "Geef aan…": hand a task to someone who may read its letter (spec S 5.3, S 13). */
export function AssignDialog({
  workspaceId,
  taskId,
  description,
  current,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  taskId: string;
  description: string;
  current: string | null;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="overlay">
      <Modal className="modal">
        <Dialog className="dialog">
          <Heading slot="title" className="dialog-title">
            {t`Geef aan`}
          </Heading>
          <p className="muted">{description}</p>
          {isOpen ? (
            <AssignForm
              workspaceId={workspaceId}
              taskId={taskId}
              current={current}
              onClose={() => onOpenChange(false)}
            />
          ) : null}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
