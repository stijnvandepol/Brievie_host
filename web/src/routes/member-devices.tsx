import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, redirect } from "@tanstack/react-router";
import { useState } from "react";
import { ApiFailure, api } from "../api/client";
import { membersQuery } from "../api/queries";
import { ConfirmAction } from "../components/confirm-dialog";
import { DeviceTable } from "../components/device-table";
import { PageHeader, Shell, usePageTitle } from "../components/page";
import { MessageBar } from "../components/ui";
import { workspaceRoute } from "./workspace";

function MemberDevicesPage() {
  const { workspace } = memberDevicesRoute.useRouteContext();
  const { userId } = memberDevicesRoute.useParams();
  const members = useQuery(membersQuery(workspace.id));
  const name = members.data?.find((member) => member.user_id === userId)?.name;
  const title = name ? t`Apparaten en sessies van ${name}` : t`Apparaten en sessies`;
  usePageTitle(title);
  const [ended, setEnded] = useState(false);
  const who = name ?? t`Dit lid`;

  async function endSessions() {
    const { error, response } = await api.DELETE("/api/v1/users/{user_id}/sessions", {
      params: { path: { user_id: userId } },
    });
    if (!response.ok) throw new ApiFailure(error);
    setEnded(true);
  }

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={title}
        breadcrumb={{
          to: "/w/$workspaceId/mensen",
          params: { workspaceId: workspace.id },
          label: t`Mensen`,
        }}
        description={t`De telefoons en browsers waarmee ${who} bij Brievie kan.`}
      />
      <DeviceTable userId={userId} ownerName={who} />
      <section className="panel panel-pad" aria-labelledby="member-sessions">
        <h2 id="member-sessions">{t`Browsers`}</h2>
        <p className="muted">{t`Meld ${who} overal af als een account misbruikt kan zijn. Gekoppelde telefoons blijven werken.`}</p>
        {ended ? (
          <MessageBar intent="success" role="status">
            {t`${who} is overal uitgelogd.`}
          </MessageBar>
        ) : null}
        <div>
          <ConfirmAction
            label={t`Alle sessies beëindigen`}
            icon="signOut"
            title={t`Alle sessies beëindigen?`}
            confirmLabel={t`Beëindigen`}
            onConfirm={endSessions}
          >
            <p>{t`${who} wordt overal uitgelogd en krijgt daar een melding van. Gekoppelde telefoons blijven werken.`}</p>
          </ConfirmAction>
        </div>
      </section>
    </Shell>
  );
}

export const memberDevicesRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "leden/$userId",
  // Only admins manage somebody else's account; the server enforces it too.
  beforeLoad: ({ context }) => {
    if (context.workspace.role !== "admin") {
      throw redirect({
        to: "/w/$workspaceId/mensen",
        params: { workspaceId: context.workspace.id },
      });
    }
  },
  component: MemberDevicesPage,
});
