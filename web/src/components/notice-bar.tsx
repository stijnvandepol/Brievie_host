import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { noticesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { MessageBar } from "./ui";

type Notice = components["schemas"]["NoticeOut"];

function NoticeItem({ notice }: { notice: Notice }) {
  const queryClient = useQueryClient();
  const seen = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/v1/me/notices/seen", {
        body: { notice_ids: [notice.id] },
      });
      if (!response.ok) throw new ApiFailure(error);
    },
    // Also after a failure: the list shows what the server still holds.
    onSettled: () => queryClient.invalidateQueries({ queryKey: noticesQuery.queryKey }),
  });
  return (
    <MessageBar intent="info" role="status">
      <span className="notice-text">{notice.message}</span>
      {notice.can_dismiss ? (
        <Button
          className="button secondary small"
          isPending={seen.isPending}
          onPress={() => seen.mutate()}
        >
          {t`Gezien`}
        </Button>
      ) : null}
    </MessageBar>
  );
}

/** Which notices a page shows (spec S 8, decision B7): a notice about a workspace only in
 * that workspace, and all of them on the overview of workspaces; one about the account
 * everywhere. */
export function shownHere(
  notice: Pick<Notice, "workspace_id">,
  where: { workspaceId?: string; all?: boolean },
): boolean {
  if (where.all || !notice.workspace_id) return true;
  return notice.workspace_id === where.workspaceId;
}

/**
 * What the server wants the user to know (a password reset by an admin, a revoked device):
 * one bar per notice with the server's own sentence, above every page it belongs on. A
 * notice that may be dismissed has "Gezien"; one that may not says nothing more and stays
 * until a later sign-in. A notice that cannot be read is no reason to disturb the page.
 */
export function NoticeBar({ workspaceId, all = false }: { workspaceId?: string; all?: boolean }) {
  const { data } = useQuery(noticesQuery);
  const shown = (data ?? []).filter((notice) => shownHere(notice, { workspaceId, all }));
  if (shown.length === 0) return null;
  return (
    <div className="notice-bar">
      {shown.map((notice) => (
        <NoticeItem key={notice.id} notice={notice} />
      ))}
    </div>
  );
}
