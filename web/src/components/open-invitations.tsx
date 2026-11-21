import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { invitationsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { ConfirmAction } from "./confirm-dialog";
import { Icon } from "./icons";
import type { CreatedInvitation } from "./invite-dialog";
import { ErrorAlert, focusPageHeading } from "./page";
import { roleLabel } from "./role-dialog";
import { Badge } from "./ui";
import { previewBody, useSightChange } from "./visibility-preview-dialog";

type Invitation = components["schemas"]["InvitationOut"];

const dateTime = dateFormat({ dateStyle: "long", timeStyle: "short" });

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
  const sight = useSightChange(workspaceId);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: invitationsQuery(workspaceId).queryKey });
  const renew = useMutation({
    // The new link is handed to the page's dialog and kept nowhere else.
    gcTime: 0,
    mutationFn: async () => {
      let made: CreatedInvitation | null = null;
      // A renewal gives its role and person again, in the state of now: what that widens
      // is confirmed again.
      await sight.tryFirst({
        preview: previewBody({
          member_change: "invite",
          member_role: invitation.role,
          member_person_id: invitation.person_id,
        }),
        confirmLabel: t`Ja, maak een nieuwe link`,
        write: async (confirmation) => {
          const { data, error } = await api.POST(
            "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/renew",
            {
              params: { path: { workspace_id: workspaceId, invitation_id: invitation.id } },
              body: confirmation,
            },
          );
          if (error !== undefined || !data) throw new ApiFailure(error);
          made = data;
        },
      });
      return made;
    },
    onSuccess: async (data) => {
      if (data === null) return;
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
      {sight.dialog}
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

/** The invitations without a person that are still open, for admins. A link is never shown
 * again: "Nieuwe link" makes another one. */
export function OpenInvitations({
  workspaceId,
  showRole,
  onRenewed,
}: {
  workspaceId: string;
  showRole: boolean;
  onRenewed: (created: CreatedInvitation) => void;
}) {
  const invitations = useQuery(invitationsQuery(workspaceId));
  // An invitation for a person shows on that person's row under Mensen.
  const open = (invitations.data ?? []).filter((invitation) => invitation.person_id === null);
  if (invitations.error) return <ErrorAlert error={invitations.error} />;
  if (open.length === 0) return null;
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
            {open.map((invitation) => (
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
