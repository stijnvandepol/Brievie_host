import { t } from "@lingui/core/macro";
import { createRoute, redirect } from "@tanstack/react-router";
import { ApiFailure, api } from "../api/client";
import { type AuditFilters, AuditLog } from "../components/audit-table";
import { PageHeader, Shell, usePageTitle } from "../components/page";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

async function fetchPage(filters: AuditFilters, before: string | undefined) {
  const { data, error } = await api.GET("/api/v1/server/audit-events", {
    params: {
      query: {
        user_id: filters.userId || undefined,
        code: filters.code || undefined,
        since: filters.since || undefined,
        until: filters.until || undefined,
        before,
      },
    },
  });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

function ServerAuditLogPage() {
  usePageTitle(t`Serverlogboek`);
  return (
    <Shell>
      <PageHeader
        title={t`Serverlogboek`}
        description={t`Inloggen, apparaten en instellingen van de hele server. Er staat nooit de inhoud van een brief in.`}
      />
      <AuditLog
        scopeKey={["server"]}
        fetchPage={fetchPage}
        csvPath="/api/v1/server/audit-events/csv"
      />
    </Shell>
  );
}

export const serverAuditLogRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/server/logboek",
  beforeLoad: async ({ context }) => {
    const user = await requireUser(context.queryClient);
    // Only a server admin reads the server's log; the server refuses everyone else too.
    if (!user.is_server_admin) throw redirect({ to: "/" });
  },
  component: ServerAuditLogPage,
});
