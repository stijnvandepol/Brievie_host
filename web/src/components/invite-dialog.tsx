import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  Disclosure,
  DisclosurePanel,
  Form,
  Heading,
  Input,
  Label,
  Modal,
  ModalOverlay,
  Text,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { invitationsQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { Icon } from "./icons";
import { OneTimeLink } from "./one-time-link";
import { ErrorAlert } from "./page";
import { type Role, RoleChoice } from "./role-dialog";
import { MessageBar } from "./ui";

export type CreatedInvitation = components["schemas"]["CreatedInvitationOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "long", timeStyle: "short" });

const EXPIRY_DAYS = [1, 7, 30] as const;
type ExpiryDays = (typeof EXPIRY_DAYS)[number];

function isExpiryDays(value: number): value is ExpiryDays {
  return (EXPIRY_DAYS as readonly number[]).includes(value);
}

function expiryLabel(days: ExpiryDays): string {
  return days === 1 ? t`1 dag` : days === 7 ? t`7 dagen` : t`30 dagen`;
}

/** The link of a made invitation, with its QR code. Rendered only while the dialog is open. */
export function InvitationLink({
  created,
  onClose,
}: {
  created: CreatedInvitation;
  onClose: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  // The button that made the link is gone: keyboard and screen reader users start here.
  useEffect(() => heading.current?.focus(), []);
  const expires = dateTime.format(new Date(created.invitation.expires_at));
  return (
    <>
      <Heading slot="title" className="dialog-title" ref={heading} tabIndex={-1}>
        {t`De uitnodiging is klaar`}
      </Heading>
      <p>
        {t`Stuur deze link, of laat de QR-code scannen. Je ziet de link maar één keer: sluit je dit venster, dan maak je een nieuwe.`}
      </p>
      {created.home_network_only ? (
        <MessageBar intent="info">{t`Deze link werkt alleen op je thuisnetwerk.`}</MessageBar>
      ) : null}
      <OneTimeLink link={created.link} />
      <div className="invite-qr">
        <QRCodeSVG
          value={created.link}
          title={t`QR-code met de uitnodiging`}
          size={180}
          marginSize={4}
          level="M"
        />
      </div>
      <p className="muted small">{t`Geldig tot ${expires}.`}</p>
      <div className="dialog-actions">
        <Button className="button" onPress={onClose}>
          {t`Klaar`}
        </Button>
      </div>
    </>
  );
}

/** A modal that shows one made invitation; closing forgets it. */
export function InvitationLinkDialog({
  created,
  onClose,
}: {
  created: CreatedInvitation | null;
  onClose: () => void;
}) {
  return (
    <ModalOverlay
      isOpen={created !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={t`Uitnodiging`}>
          {created ? <InvitationLink created={created} onClose={onClose} /> : null}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function InviteForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (created: CreatedInvitation) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: persons } = useQuery(personsQuery(workspaceId));
  const [personId, setPersonId] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [days, setDays] = useState<ExpiryDays>(7);
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  // Somebody who already has an account is no one to invite.
  const free = (persons ?? []).filter((person) => person.user_id === null && !person.archived);

  const create = useMutation({
    // The result goes to the caller's state; the mutation cache keeps nothing of it.
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/invitations", {
        params: { path: { workspace_id: workspaceId } },
        body: {
          role,
          expires_in_days: days,
          email: email.trim(),
          person_id: personId === "" ? null : personId,
          note: note.trim(),
        },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (data) => {
      onCreated(data);
      await queryClient.invalidateQueries({ queryKey: invitationsQuery(workspaceId).queryKey });
    },
  });

  return (
    <>
      <Heading slot="title" className="dialog-title">
        {t`Iemand uitnodigen`}
      </Heading>
      <p>{t`Je krijgt een link en een QR-code. Wie die opent, maakt een account of logt in en hoort daarna bij deze werkruimte.`}</p>
      <Form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        {free.length > 0 ? (
          <div className="field">
            <label htmlFor="invite-person">{t`Wie is het?`}</label>
            <select
              id="invite-person"
              value={personId}
              onChange={(event) => setPersonId(event.target.value)}
            >
              <option value="">{t`Iemand nieuw`}</option>
              {free.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.display_name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <Disclosure className="disclosure">
          <Heading level={3} className="disclosure-heading">
            <Button slot="trigger" className="disclosure-trigger">
              <Icon name="chevronRight" className="disclosure-chevron" />
              {t`Meer opties`}
            </Button>
          </Heading>
          <DisclosurePanel className="disclosure-panel">
            <div className="form">
              <RoleChoice value={role} onChange={setRole} label={t`Rol`} />
              <div className="field">
                <label htmlFor="invite-days">{t`Geldig`}</label>
                <select
                  id="invite-days"
                  value={days}
                  onChange={(event) => {
                    const next = Number(event.target.value);
                    if (isExpiryDays(next)) setDays(next);
                  }}
                >
                  {EXPIRY_DAYS.map((option) => (
                    <option key={option} value={option}>
                      {expiryLabel(option)}
                    </option>
                  ))}
                </select>
              </div>
              <TextField type="email" className="field" value={email} onChange={setEmail}>
                <Label>{t`E-mailadres`}</Label>
                <Text slot="description">{t`Alleen dit adres kan de uitnodiging gebruiken.`}</Text>
                <Input autoComplete="off" />
              </TextField>
              <TextField className="field" value={note} onChange={setNote} maxLength={100}>
                <Label>{t`Notitie voor jezelf`}</Label>
                <Input autoComplete="off" />
              </TextField>
            </div>
          </DisclosurePanel>
        </Disclosure>
        <ErrorAlert error={create.error} />
        <div className="dialog-actions">
          <Button className="button secondary" onPress={onCancel}>
            {t`Annuleren`}
          </Button>
          <Button type="submit" className="button" isPending={create.isPending}>
            {t`Link maken`}
          </Button>
        </div>
      </Form>
    </>
  );
}

/**
 * Inviting somebody: one button makes a link for a member, for seven days. Role, expiry,
 * a fixed address and a note sit behind "Meer opties". The made link is shown in this
 * dialog only; closing it forgets the link.
 */
export function InviteDialog({
  workspaceId,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  // Forget the link as soon as the dialog is closed, however it was closed.
  useEffect(() => {
    if (!isOpen) setCreated(null);
  }, [isOpen]);
  const close = () => onOpenChange(false);
  return (
    <ModalOverlay
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      // The link is shown once: a stray click beside the dialog must not throw it away.
      isDismissable={created === null}
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={created ? t`Uitnodiging` : t`Iemand uitnodigen`}>
          {created ? (
            <InvitationLink created={created} onClose={close} />
          ) : (
            <InviteForm workspaceId={workspaceId} onCreated={setCreated} onCancel={close} />
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
