import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Button } from "react-aria-components";
import { errorMessage } from "../api/client";
import { refreshAfterTrash, restoreDocument } from "../api/trash";
import { clearUndo, useUndo } from "../undo-trash";
import { MessageBar } from "./ui";

/**
 * "De brief staat in de prullenbak." with "Ongedaan maken" and "Sluiten", after a letter was
 * trashed in this workspace. No timer: it stays until it is closed (WCAG 2.2.1).
 */
export function UndoBar({ workspaceId }: { workspaceId: string }) {
  const undo = useUndo();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const restore = useMutation({
    mutationFn: (documentId: string) => restoreDocument(workspaceId, documentId),
    onSuccess: async (document) => {
      clearUndo();
      queryClient.setQueryData(["workspaces", workspaceId, "documents", document.id], document);
      await navigate({
        to: "/w/$workspaceId/post/$documentId",
        params: { workspaceId, documentId: document.id },
      });
      await refreshAfterTrash(queryClient, workspaceId);
    },
  });
  if (undo === null || undo.workspaceId !== workspaceId) return null;
  return (
    <div className="notice-bar">
      <MessageBar intent="success" role="status">
        <span className="notice-text">{t`De brief staat in de prullenbak.`}</span>
        <span className="row-actions">
          <Button
            className="button secondary small"
            isPending={restore.isPending}
            onPress={() => restore.mutate(undo.documentId)}
          >
            {t`Ongedaan maken`}
          </Button>
          <Button className="button-text small" onPress={clearUndo}>
            {t`Sluiten`}
          </Button>
        </span>
      </MessageBar>
      {restore.error ? (
        // Not `ErrorAlert` from page.tsx: the shell draws this bar, so that would import in a circle.
        <MessageBar intent="error" role="alert">
          {errorMessage(restore.error)}
        </MessageBar>
      ) : null}
    </div>
  );
}
