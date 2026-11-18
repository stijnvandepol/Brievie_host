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

/**
 * What the server wants the user to know (a password reset by an admin, a revoked device):
 * one bar per notice with the server's own sentence, above every page. A notice that may be
 * dismissed has "Gezien"; one that may not says nothing more and stays until a later
 * sign-in. A notice that cannot be read is no reason to disturb the page.
 */
export function NoticeBar() {
  const { data } = useQuery(noticesQuery);
  if (!data || data.length === 0) return null;
  return (
    <div className="notice-bar">
      {data.map((notice) => (
        <NoticeItem key={notice.id} notice={notice} />
      ))}
    </div>
  );
}
