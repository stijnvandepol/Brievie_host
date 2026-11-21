import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Dialog, Form, Heading, Modal, ModalOverlay } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { membersQuery, meQuery, visibilityQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert } from "./page";
import { CheckRow } from "./ui";

type Document = components["schemas"]["DocumentOut"];
type Visibility = "restricted" | "workspace";

/** Sets who reads a letter: private or not, and with whom it is shared (on every letter,
 * R44). Every list that shows the letter is fetched again; `can_read` says whether the
 * caller still reads it. */
export function useSetVisibility(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { visibility: Visibility; shared_user_ids: string[] }) => {
      const { data, error } = await api.PUT(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/visibility",
        {
          params: { path: { workspace_id: workspaceId, document_id: documentId } },
          body,
        },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "inbox-counts"] }),
      ]),
  });
}

/**
 * "Privé maken" (`mode` private: "Jij" stays, others are ticked) or "Delen met" (`mode`
 * share: the letter's current shares; on a private letter "Jij" too, with the warning that
 * unticking it may shut you out). `onGone` runs when the caller can no longer read it.
 */
export function ShareDialog({
  workspaceId,
  document,
  mode,
  onClose,
  onGone,
}: {
  workspaceId: string;
  document: Document;
  mode: "private" | "share";
  onClose: () => void;
  onGone: () => void;
}) {
  const { data: me } = useQuery(meQuery);
  const { data: members } = useQuery(membersQuery(workspaceId));
  const save = useSetVisibility(workspaceId, document.id);
  const restricted = document.visibility === "restricted";
  const shared = document.shared_user_ids ?? [];
  const { data: sight } = useQuery(visibilityQuery(workspaceId));
  const home = sight?.kind === "home";
  // Both start from the current shares: an earlier share is visible and can be unticked
  // (the server then removes it), never kept silently.
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(shared));
  const myId = me?.id ?? "";
  // On a private letter your own share can be taken away; on any other letter you are no
  // share of your own.
  const ownShare = mode === "share" && restricted && shared.includes(myId);
  const others = (members ?? []).filter((member) => member.user_id !== myId);
  const title = mode === "private" ? t`Privé maken` : t`Delen met`;

  function toggle(userId: string, on: boolean) {
    const next = new Set(chosen);
    if (on) next.add(userId);
    else next.delete(userId);
    setChosen(next);
  }

  async function submit() {
    // Your own share is the dialog's "Jij": fixed when making private, a tick otherwise.
    const ids = [...chosen].filter((id) => id !== myId || ownShare);
    const answer = await save.mutateAsync({
      visibility: mode === "private" || restricted ? "restricted" : "workspace",
      shared_user_ids: ids,
    });
    if (answer.can_read === false) onGone();
    else onClose();
  }

  return (
    <ModalOverlay
      isOpen
      onOpenChange={(open) => !open && onClose()}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={title}>
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          <p>
            {mode === "private"
              ? home
                ? t`Alleen jij, wie de brief scande, de mensen op wier naam hij staat, de mensen van een groep op wier naam hij staat, en wie je hier aanvinkt, kunnen hem dan lezen.`
                : t`Alleen jij, wie de brief scande, de mensen op wier naam hij staat, de mensen van een afdeling op wier naam hij staat, en wie je hier aanvinkt, kunnen hem dan lezen.`
              : t`Wie je aanvinkt, kan deze brief lezen, ook als die anders niet bij hem komt.`}
          </p>
          <Form
            className="form"
            onSubmit={(event) => {
              event.preventDefault();
              submit().catch(() => {
                // Shown in the dialog (save.error).
              });
            }}
          >
            <fieldset className="check-group">
              <legend>{t`Delen met`}</legend>
              {mode === "private" ? (
                <CheckRow checked disabled onChange={() => {}}>
                  {t`Jij`}
                </CheckRow>
              ) : null}
              {ownShare ? (
                <CheckRow
                  checked={chosen.has(myId)}
                  onChange={(on) => toggle(myId, on)}
                  hint={
                    chosen.has(myId)
                      ? undefined
                      : t`Daarna kun je deze brief zelf misschien niet meer openen.`
                  }
                >
                  {t`Jij`}
                </CheckRow>
              ) : null}
              {others.map((member) => (
                <CheckRow
                  key={member.user_id}
                  checked={chosen.has(member.user_id)}
                  onChange={(on) => toggle(member.user_id, on)}
                >
                  {member.name}
                </CheckRow>
              ))}
            </fieldset>
            <ErrorAlert error={save.error} />
            <div className="dialog-actions">
              <Button className="button secondary" onPress={onClose}>
                {t`Annuleren`}
              </Button>
              <Button type="submit" className="button" isPending={save.isPending}>
                {mode === "private" ? t`Privé maken` : t`Opslaan`}
              </Button>
            </div>
          </Form>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
