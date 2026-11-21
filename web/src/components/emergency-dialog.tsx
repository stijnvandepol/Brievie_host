import { plural, t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import {
  Button,
  Dialog,
  Form,
  Heading,
  Label,
  Modal,
  ModalOverlay,
  Text,
  TextArea,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { ErrorAlert } from "./page";
import { MessageBar } from "./ui";

export const REASON_MIN = 10;
export const REASON_MAX = 200;

/**
 * Emergency access (S 4.7): for one hour, read-only, every letter on this person's or
 * department's name the admin cannot see now. A reason of 10 to 200 characters; the log
 * keeps it and the people concerned see it with the letters.
 */
export function EmergencyDialog({
  workspaceId,
  person,
  onClose,
}: {
  workspaceId: string;
  person: { id: string; display_name: string };
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const length = reason.trim().length;
  const fits = length >= REASON_MIN && length <= REASON_MAX;
  const name = person.display_name;
  const open = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/persons/{person_id}/emergency-access",
        {
          params: { path: { workspace_id: workspaceId, person_id: person.id } },
          body: { reason: reason.trim() },
        },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  const title = t`Noodtoegang tot de post van ${name}`;
  return (
    <ModalOverlay
      isOpen
      onOpenChange={(isOpen) => !isOpen && onClose()}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={title}>
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          {open.data ? (
            <>
              <MessageBar intent="success" role="status">
                {open.data.opened_count === 0
                  ? t`Er is geen post van ${name} die je nog niet kon zien.`
                  : plural(open.data.opened_count, {
                      one: "Je kunt # brief een uur lang openen.",
                      other: "Je kunt # brieven een uur lang openen.",
                    })}
              </MessageBar>
              <div className="dialog-actions">
                <Button className="button" onPress={onClose}>
                  {t`Sluiten`}
                </Button>
              </div>
            </>
          ) : (
            <Form
              className="form"
              onSubmit={(event) => {
                event.preventDefault();
                if (fits) open.mutate();
              }}
            >
              <p>{t`Je kunt de post van ${name} een uur lang lezen, maar niets wijzigen. Dit komt in het logboek, de lezers zien het bij de brief en krijgen een melding.`}</p>
              <TextField className="field" value={reason} onChange={setReason} isRequired>
                <Label>{t`Reden`}</Label>
                <Text slot="description">
                  {t`${length} van ${REASON_MAX} tekens, minstens ${REASON_MIN}.`}
                </Text>
                <TextArea rows={3} maxLength={REASON_MAX} />
              </TextField>
              <ErrorAlert error={open.error} />
              <div className="dialog-actions">
                <Button className="button secondary" onPress={onClose}>
                  {t`Annuleren`}
                </Button>
                <Button
                  type="submit"
                  className="button"
                  isDisabled={!fits}
                  isPending={open.isPending}
                >
                  {t`Openen`}
                </Button>
              </div>
            </Form>
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
