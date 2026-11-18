import { t } from "@lingui/core/macro";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { membersQuery, meQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmAction } from "../components/confirm-dialog";
import { Icon } from "../components/icons";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { RoleDialog, roleLabel } from "../components/role-dialog";
import { Badge, EmptyState, Loading, Persona } from "../components/ui";
import { workspaceRoute } from "./workspace";

type Member = components["schemas"]["MemberOut"];

function MemberRow({
  workspaceId,
  workspaceName,
  member,
  isOwn,
  isAdmin,
}: {
  workspaceId: string;
  workspaceName: string;
  member: Member;
  isOwn: boolean;
  isAdmin: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [roleOpen, setRoleOpen] = useState(false);

  async function remove() {
    const { error, response } = await api.DELETE(
      "/api/v1/workspaces/{workspace_id}/members/{user_id}",
      { params: { path: { workspace_id: workspaceId, user_id: member.user_id } } },
    );
    if (!response.ok) throw new ApiFailure(error);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: membersQuery(workspaceId).queryKey }),
      queryClient.invalidateQueries({ queryKey: personsQuery(workspaceId).queryKey }),
    ]);
    // The dialog gave focus back to this row's button, which is gone now.
    focusPageHeading();
  }

  async function leave() {
    const { error, response } = await api.POST("/api/v1/workspaces/{workspace_id}/leave", {
      params: { path: { workspace_id: workspaceId } },
    });
    if (!response.ok) throw new ApiFailure(error);
    // Forget everything of this workspace first, then ask the server who I am now: with
    // another workspace left the list opens, with none the account is off and the guard
    // sends me to the sign-in page.
    queryClient.removeQueries({ queryKey: ["workspaces"] });
    await navigate({ to: "/" });
  }

  return (
    <tr>
      <th scope="row" className="cell-strong">
        <Persona name={member.name} />
      </th>
      <td>
        <Badge>{roleLabel(member.role)}</Badge>
      </td>
      <td className="cell-action">
        <span className="row-actions">
          {isAdmin ? (
            <>
              <Button
                className="button-text"
                aria-label={t`Rol wijzigen: ${member.name}`}
                onPress={() => setRoleOpen(true)}
              >
                <Icon name="edit" />
                {t`Rol wijzigen`}
              </Button>
              <RoleDialog
                workspaceId={workspaceId}
                member={member}
                isOpen={roleOpen}
                onOpenChange={setRoleOpen}
              />
            </>
          ) : null}
          {isAdmin && !isOwn ? (
            <ConfirmAction
              label={t`Verwijderen`}
              ariaLabel={t`Verwijderen: ${member.name}`}
              icon="delete"
              title={t`${member.name} verwijderen?`}
              confirmLabel={t`Verwijderen`}
              onConfirm={remove}
            >
              <p>
                {t`${member.name} kan daarna niet meer bij deze werkruimte. De post blijft hier. Taken van ${member.name} hebben daarna niemand.`}
              </p>
            </ConfirmAction>
          ) : null}
          {isOwn ? (
            <ConfirmAction
              label={t`Werkruimte verlaten`}
              icon="signOut"
              title={t`Werkruimte verlaten?`}
              confirmLabel={t`Verlaten`}
              onConfirm={leave}
            >
              <p>
                {t`Je kunt daarna niet meer bij ${workspaceName}. De post blijft hier voor de andere leden.`}
              </p>
            </ConfirmAction>
          ) : null}
        </span>
      </td>
    </tr>
  );
}

function MembersPage() {
  usePageTitle(t`Leden`);
  const { workspace } = membersRoute.useRouteContext();
  const members = useQuery(membersQuery(workspace.id));
  const { data: me } = useQuery(meQuery);
  const isAdmin = workspace.role === "admin";

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Leden`}
        description={t`De mensen die bij ${workspace.name} horen en bij de post kunnen.`}
      />
      {members.isPending ? <Loading /> : null}
      <ErrorAlert error={members.error} />
      {members.data && members.data.length <= 1 ? (
        <EmptyState icon="people">
          {isAdmin
            ? t`Je bent hier nog alleen. Nodig iemand uit om samen de post te doen.`
            : t`Je bent hier nog alleen.`}
        </EmptyState>
      ) : null}
      {members.data ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Leden`} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Naam`}</th>
                <th scope="col">{t`Rol`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.data.map((member) => (
                <MemberRow
                  key={member.user_id}
                  workspaceId={workspace.id}
                  workspaceName={workspace.name}
                  member={member}
                  isOwn={member.user_id === me?.id}
                  isAdmin={isAdmin}
                />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </Shell>
  );
}

export const membersRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "leden",
  component: MembersPage,
});
