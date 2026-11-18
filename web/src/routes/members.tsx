import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { invitationsQuery, membersQuery, meQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmAction } from "../components/confirm-dialog";
import { Icon } from "../components/icons";
import {
  type CreatedInvitation,
  InvitationLinkDialog,
  InviteDialog,
} from "../components/invite-dialog";
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

type Invitation = components["schemas"]["InvitationOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "long", timeStyle: "short" });

/** What an invitation is for, as its maker knows it: the person, the note or the address. */
function invitationName(invitation: Invitation): string {
  return (
    invitation.person_name || invitation.note || invitation.email || t`Uitnodiging zonder naam`
  );
}

function InvitationRow({
  workspaceId,
  invitation,
  onRenewed,
}: {
  workspaceId: string;
  invitation: Invitation;
  onRenewed: (created: CreatedInvitation) => void;
}) {
  const queryClient = useQueryClient();
  const name = invitationName(invitation);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: invitationsQuery(workspaceId).queryKey });
  const renew = useMutation({
    // The new link is handed to the page's dialog and kept nowhere else.
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/renew",
        { params: { path: { workspace_id: workspaceId, invitation_id: invitation.id } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (data) => {
      onRenewed(data);
      renew.reset();
      await refresh();
    },
  });

  async function revoke() {
    const { error, response } = await api.DELETE(
      "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}",
      { params: { path: { workspace_id: workspaceId, invitation_id: invitation.id } } },
    );
    if (!response.ok) throw new ApiFailure(error);
    await refresh();
    focusPageHeading();
  }

  return (
    <>
      <tr>
        <th scope="row" className="cell-strong">
          {name}
          {invitation.email && invitation.email !== name ? (
            <span className="cell-hint">{invitation.email}</span>
          ) : null}
        </th>
        <td>
          <Badge>{roleLabel(invitation.role)}</Badge>
        </td>
        <td>
          {invitation.status === "expired" ? (
            <Badge intent="warning">{t`Verlopen`}</Badge>
          ) : (
            t`Verloopt op ${dateTime.format(new Date(invitation.expires_at))}`
          )}
        </td>
        <td className="cell-action">
          <span className="row-actions">
            <Button
              className="button-text"
              aria-label={t`Nieuwe link: ${name}`}
              isPending={renew.isPending}
              onPress={() => renew.mutate()}
            >
              <Icon name="refresh" />
              {t`Nieuwe link`}
            </Button>
            <ConfirmAction
              label={t`Intrekken`}
              ariaLabel={t`Intrekken: ${name}`}
              icon="dismiss"
              title={t`Uitnodiging intrekken?`}
              confirmLabel={t`Intrekken`}
              onConfirm={revoke}
            >
              <p>{t`De link werkt daarna niet meer. Wie hem al heeft, kan er niet meer mee binnenkomen.`}</p>
            </ConfirmAction>
          </span>
        </td>
      </tr>
      {renew.isError ? (
        <tr>
          <td colSpan={4}>
            <ErrorAlert error={renew.error} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** The invitations that are still open, for admins. A link is never shown again: "Nieuwe
 * link" makes another one. */
function OpenInvitations({
  workspaceId,
  onRenewed,
}: {
  workspaceId: string;
  onRenewed: (created: CreatedInvitation) => void;
}) {
  const invitations = useQuery(invitationsQuery(workspaceId));
  if (invitations.error) return <ErrorAlert error={invitations.error} />;
  if (!invitations.data || invitations.data.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="open-invitations">
      <div className="panel-head">
        <h2 id="open-invitations">{t`Open uitnodigingen`}</h2>
      </div>
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1). */}
      <div className="table-scroll" tabIndex={0}>
        <table className="table table-wide">
          <thead>
            <tr>
              <th scope="col">{t`Voor`}</th>
              <th scope="col">{t`Rol`}</th>
              <th scope="col">{t`Geldig`}</th>
              <th scope="col">
                <span className="visually-hidden">{t`Acties`}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {invitations.data.map((invitation) => (
              <InvitationRow
                key={invitation.id}
                workspaceId={workspaceId}
                invitation={invitation}
                onRenewed={onRenewed}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MembersPage() {
  usePageTitle(t`Leden`);
  const { workspace } = membersRoute.useRouteContext();
  const members = useQuery(membersQuery(workspace.id));
  const { data: me } = useQuery(meQuery);
  const isAdmin = workspace.role === "admin";
  const [inviting, setInviting] = useState(false);
  const [renewed, setRenewed] = useState<CreatedInvitation | null>(null);

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Leden`}
        description={t`De mensen die bij ${workspace.name} horen en bij de post kunnen.`}
        commands={
          isAdmin ? (
            <Button className="button" onPress={() => setInviting(true)}>
              <Icon name="personAdd" />
              {t`Uitnodigen`}
            </Button>
          ) : null
        }
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
      {isAdmin ? <OpenInvitations workspaceId={workspace.id} onRenewed={setRenewed} /> : null}
      {isAdmin ? (
        <>
          <InviteDialog workspaceId={workspace.id} isOpen={inviting} onOpenChange={setInviting} />
          <InvitationLinkDialog created={renewed} onClose={() => setRenewed(null)} />
        </>
      ) : null}
    </Shell>
  );
}

export const membersRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "leden",
  component: MembersPage,
});
