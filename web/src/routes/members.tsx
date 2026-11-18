import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button, Dialog, DialogTrigger, Popover } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import {
  invitationsQuery,
  membersQuery,
  meQuery,
  myExportQuery,
  personsQuery,
  workspacesQuery,
} from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmAction } from "../components/confirm-dialog";
import { ExportPanel } from "../components/export-panel";
import { Icon } from "../components/icons";
import {
  type CreatedInvitation,
  InvitationLinkDialog,
  InviteDialog,
} from "../components/invite-dialog";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { ResetLinkDialog } from "../components/reset-link-dialog";
import { RoleDialog, roleLabel } from "../components/role-dialog";
import { Badge, EmptyState, Loading, Persona } from "../components/ui";
import { workspaceRoute } from "./workspace";

type Member = components["schemas"]["MemberOut"];

/** In the leave dialog: the export, while the workspace allows it. After leaving nothing
 * can be downloaded any more. */
function LeaveExportOffer({ workspaceId, isAdmin }: { workspaceId: string; isAdmin: boolean }) {
  const { data } = useQuery(myExportQuery(workspaceId));
  if (!data?.allowed) return null;
  return (
    <>
      <p>{t`Exporteer eerst je post: na het verlaten kun je niet meer downloaden.`}</p>
      <ExportPanel workspaceId={workspaceId} isAdmin={isAdmin} />
    </>
  );
}

function MemberRow({
  workspaceId,
  workspaceName,
  member,
  isOwn,
  isAdmin,
  showRole,
}: {
  workspaceId: string;
  workspaceName: string;
  member: Member;
  isOwn: boolean;
  isAdmin: boolean;
  /** Not in a home workspace: there roles are an option, not something to look at. */
  showRole: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [roleOpen, setRoleOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);

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
    // The list of workspaces has no observer here: drop it so the next page asks again.
    queryClient.removeQueries({ queryKey: workspacesQuery.queryKey, exact: true });
    // Leave the page first, so nothing on it asks the server again: with another workspace
    // left the list opens, with none the account is off and the guard sends me to sign-in.
    await navigate({ to: "/" });
    // Only now forget what belonged to this workspace.
    await queryClient.cancelQueries({ queryKey: ["workspaces", workspaceId] });
    queryClient.removeQueries({ queryKey: ["workspaces", workspaceId] });
  }

  return (
    <tr>
      <th scope="row" className="cell-strong">
        <Persona name={member.name} />
      </th>
      {showRole ? (
        <td>
          <Badge>{roleLabel(member.role)}</Badge>
        </td>
      ) : null}
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
          {isAdmin && member.can_manage_account && !isOwn ? (
            <>
              <DialogTrigger>
                <Button className="button-text" aria-label={t`Account: ${member.name}`}>
                  {t`Account`}
                  <Icon name="chevronDown" />
                </Button>
                <Popover className="popover account-popover" placement="bottom end" offset={4}>
                  <Dialog className="account-dialog" aria-label={t`Account van ${member.name}`}>
                    {({ close }) => (
                      <>
                        <Link
                          to="/w/$workspaceId/leden/$userId"
                          params={{ workspaceId, userId: member.user_id }}
                          className="menu-item"
                          onClick={close}
                        >
                          <Icon name="phone" />
                          {t`Apparaten en sessies`}
                        </Link>
                        <Button
                          className="menu-item"
                          onPress={() => {
                            close();
                            setResetOpen(true);
                          }}
                        >
                          <Icon name="link" />
                          {t`Herstellink maken`}
                        </Button>
                      </>
                    )}
                  </Dialog>
                </Popover>
              </DialogTrigger>
              <ResetLinkDialog
                userId={member.user_id}
                name={member.name}
                isOpen={resetOpen}
                onOpenChange={setResetOpen}
              />
            </>
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
              <LeaveExportOffer workspaceId={workspaceId} isAdmin={isAdmin} />
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
  showRole,
  onRenewed,
}: {
  workspaceId: string;
  invitation: Invitation;
  showRole: boolean;
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
        {showRole ? (
          <td>
            <Badge>{roleLabel(invitation.role)}</Badge>
          </td>
        ) : null}
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
          <td colSpan={showRole ? 4 : 3}>
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
  showRole,
  onRenewed,
}: {
  workspaceId: string;
  showRole: boolean;
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
              {showRole ? <th scope="col">{t`Rol`}</th> : null}
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
                showRole={showRole}
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
  // At home nothing about roles shows unless somebody opens "Rol wijzigen".
  const showRole = workspace.kind !== "home";
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
                {showRole ? <th scope="col">{t`Rol`}</th> : null}
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
                  showRole={showRole}
                />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      <section className="panel panel-pad" aria-labelledby="my-export">
        <h2 id="my-export">{t`Mijn post`}</h2>
        <p className="muted">{t`Maak een ZIP-bestand met jouw post. Het staat 24 uur klaar om te downloaden.`}</p>
        <ExportPanel workspaceId={workspace.id} isAdmin={isAdmin} />
      </section>
      {isAdmin ? (
        <OpenInvitations workspaceId={workspace.id} showRole={showRole} onRenewed={setRenewed} />
      ) : null}
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
