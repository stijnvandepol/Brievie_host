import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, redirect } from "@tanstack/react-router";
import { ApiFailure, api } from "../api/client";
import { membersQuery } from "../api/queries";
import { WORKSPACE_CODES } from "../components/audit-codes";
import { type AuditFilters, AuditLog } from "../components/audit-table";
import { PageHeader, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

function AuditLogPage() {
  usePageTitle(t`Logboek`);
  const { workspace } = auditLogRoute.useRouteContext();
  const members = useQuery(membersQuery(workspace.id));

  async function fetchPage(filters: AuditFilters, before: string | undefined) {
    const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/audit-events", {
      params: {
        path: { workspace_id: workspace.id },
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

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Logboek`}
        breadcrumb={{
          to: "/w/$workspaceId/instellingen",
          params: { workspaceId: workspace.id },
          label: t`Instellingen`,
        }}
        description={t`Wie wat heeft gedaan in ${workspace.name}. Er staat nooit de inhoud van een brief in.`}
      />
      <AuditLog
        scopeKey={["workspaces", workspace.id]}
        fetchPage={fetchPage}
        csvPath={`/api/v1/workspaces/${workspace.id}/audit-events/csv`}
        codes={WORKSPACE_CODES}
        people={members.data ?? []}
      />
    </Shell>
  );
}

export const auditLogRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "instellingen/logboek",
  // The log is for admins; the server refuses everyone else too.
  beforeLoad: ({ context }) => {
    if (context.workspace.role !== "admin") {
      throw redirect({
        to: "/w/$workspaceId/inbox",
        params: { workspaceId: context.workspace.id },
      });
    }
  },
  component: AuditLogPage,
});
