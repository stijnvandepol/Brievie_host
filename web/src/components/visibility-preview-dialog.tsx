import { plural, t } from "@lingui/core/macro";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { ErrorAlert } from "./page";
import { MessageBar } from "./ui";

export type VisibilityPreview = components["schemas"]["VisibilityPreviewOut"];
export type VisibilityPreviewBody = components["schemas"]["VisibilityPreviewIn"];
/** What a write that may widen sight sends with it (S 4.4): the admin's yes and the
 * token of the preview they said yes to. */
export type Confirmation = { confirmed: boolean; preview_token: string };

export const NOT_CONFIRMED: Confirmation = { confirmed: false, preview_token: "" };

/** A preview request with the server's defaults filled in. */
export function previewBody(change: Partial<VisibilityPreviewBody>): VisibilityPreviewBody {
  return {
    add_start_set: false,
    apply_preset: false,
    member_change: "",
    member_role: "",
    member_sorts_post: false,
    ...change,
  };
}

/** Asks the server what a change of sight would do (nothing changes). */
export async function fetchVisibilityPreview(
  workspaceId: string,
  body: VisibilityPreviewBody,
): Promise<VisibilityPreview> {
  const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/visibility/preview", {
    params: { path: { workspace_id: workspaceId } },
    body,
  });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

function ChangeList({
  title,
  lines,
}: {
  title: string;
  lines: { user_id: string; name: string; kinds: string[] }[];
}) {
  if (lines.length === 0) return null;
  return (
    <section className="preview-group" aria-label={title}>
      <h3>{title}</h3>
      <ul className="preview-list">
        {lines.map((line) => (
          <li key={line.user_id}>
            <strong>{line.name}</strong>: {line.kinds.join(", ")}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * What a change of sight does before it is made: who gains, who loses, how many of the
 * letters you can read change viewers, and how it will be. When somebody gains, the button
 * says what happens ("Ja, iedereen mag alle post zien"); otherwise it says "Wijzigen".
 */
export function VisibilityPreviewDialog({
  preview,
  isOpen,
  onOpenChange,
  confirmLabel,
  onConfirm,
  isPending = false,
  error,
  notice,
}: {
  preview: VisibilityPreview | null;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  confirmLabel: string;
  onConfirm: () => void;
  isPending?: boolean;
  error?: unknown;
  /** The server's sentence when the preview had to be made again (`preview_outdated`). */
  notice?: string;
}) {
  const changes = preview?.changes ?? [];
  const count = changes.length;
  const gains = changes
    .filter((change) => change.gains.length > 0)
    .map((change) => ({ user_id: change.user_id, name: change.name, kinds: change.gains }));
  const losses = changes
    .filter((change) => change.losses.length > 0)
    .map((change) => ({ user_id: change.user_id, name: change.name, kinds: change.losses }));
  const letters = preview?.letter_count ?? 0;
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="overlay">
      <Modal className="modal">
        <Dialog role="alertdialog" className="dialog">
          <Heading slot="title" className="dialog-title">
            {count === 0
              ? t`Hiermee gaat niemand iets anders zien.`
              : plural(count, {
                  one: "Dit verandert voor # persoon wat die ziet.",
                  other: "Dit verandert voor # mensen wat ze zien.",
                })}
          </Heading>
          {notice ? <MessageBar intent="warning">{notice}</MessageBar> : null}
          <ChangeList title={t`Gaan meer zien`} lines={gains} />
          <ChangeList title={t`Gaan minder zien`} lines={losses} />
          {letters > 0 ? (
            <p>
              {plural(letters, {
                one: "Het gaat om # brief die jij kunt lezen.",
                other: "Het gaat om # brieven die jij kunt lezen.",
              })}
            </p>
          ) : null}
          {preview && preview.sentences.length > 0 ? (
            <section className="preview-group" aria-label={t`Zo wordt het`}>
              <h3>{t`Zo wordt het`}</h3>
              <ul className="preview-list">
                {preview.sentences.map((sentence) => (
                  <li key={sentence}>{sentence}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <ErrorAlert error={error} />
          <div className="dialog-actions">
            <Button autoFocus className="button secondary" onPress={() => onOpenChange(false)}>
              {t`Annuleren`}
            </Button>
            <Button className="button" isPending={isPending} onPress={onConfirm}>
              {preview?.needs_confirmation ? confirmLabel : t`Wijzigen`}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

type Run = {
  preview: VisibilityPreviewBody;
  confirmLabel: string;
  write: (confirmation: Confirmation) => Promise<unknown>;
};

/**
 * One way for every change of sight (switches, kind, role, sorter, link, department member,
 * invitation). `previewFirst` shows the preview before anything is written (the switches:
 * also who sees less); `tryFirst` writes at once and asks only when the server answers 409
 * `confirmation_required`. A 409 `preview_outdated` (another admin changed something) makes
 * the preview again and shows the server's sentence. Both resolve `true` when the change was
 * made and `false` when the admin cancelled; another refusal rejects with it.
 */
export function useSightChange(workspaceId: string) {
  const queryClient = useQueryClient();
  // After any change of sight, everything of the workspace is asked again: a letter, a
  // count or a task the caller no longer sees must not linger in the cache.
  const refreshAll = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId] }),
    [queryClient, workspaceId],
  );
  const [run, setRun] = useState<Run | null>(null);
  const [preview, setPreview] = useState<VisibilityPreview | null>(null);
  const [notice, setNotice] = useState<string | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const settle = useRef<((done: boolean) => void) | null>(null);

  const close = useCallback((done: boolean) => {
    setRun(null);
    setPreview(null);
    setNotice(undefined);
    setError(undefined);
    setPending(false);
    settle.current?.(done);
    settle.current = null;
  }, []);

  const ask = useCallback(
    (next: Run) =>
      new Promise<boolean>((resolve, reject) => {
        settle.current = resolve;
        fetchVisibilityPreview(workspaceId, next.preview).then(
          (answer) => {
            setPreview(answer);
            setRun(next);
          },
          (reason: unknown) => {
            settle.current = null;
            reject(reason);
          },
        );
      }),
    [workspaceId],
  );

  const previewFirst = useCallback((next: Run) => ask(next), [ask]);

  const tryFirst = useCallback(
    async (next: Run) => {
      try {
        await next.write(NOT_CONFIRMED);
        await refreshAll();
        return true;
      } catch (reason) {
        if (reason instanceof ApiFailure && reason.code === "confirmation_required") {
          return ask(next);
        }
        throw reason;
      }
    },
    [ask, refreshAll],
  );

  async function confirm() {
    if (run === null || preview === null) return;
    setPending(true);
    setError(undefined);
    try {
      await run.write({ confirmed: true, preview_token: preview.preview_token });
      await refreshAll();
      close(true);
    } catch (reason) {
      setPending(false);
      if (reason instanceof ApiFailure && reason.code === "preview_outdated") {
        setNotice(reason.message);
        try {
          setPreview(await fetchVisibilityPreview(workspaceId, run.preview));
        } catch (again) {
          setError(again);
        }
        return;
      }
      setError(reason);
    }
  }

  const dialog = (
    <VisibilityPreviewDialog
      preview={preview}
      isOpen={run !== null}
      onOpenChange={(open) => {
        if (!open) close(false);
      }}
      confirmLabel={run?.confirmLabel ?? ""}
      onConfirm={() => void confirm()}
      isPending={pending}
      error={error}
      notice={notice}
    />
  );

  return { previewFirst, tryFirst, dialog };
}
