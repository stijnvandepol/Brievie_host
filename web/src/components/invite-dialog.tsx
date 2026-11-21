import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  Disclosure,
  DisclosurePanel,
  FieldError,
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
import { invitationsQuery, personPrivatePostQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { Icon } from "./icons";
import { OneTimeLink } from "./one-time-link";
import { ErrorAlert } from "./page";
import { type Role, RoleChoice } from "./role-dialog";
import { MessageBar } from "./ui";
import { previewBody, useSightChange } from "./visibility-preview-dialog";

export type CreatedInvitation = components["schemas"]["CreatedInvitationOut"];

const dateTime = dateFormat({ dateStyle: "long", timeStyle: "short" });

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
  renewed = false,
}: {
  created: CreatedInvitation;
  onClose: () => void;
  /** A new link for an invitation that was sent before: the old one stopped. */
  renewed?: boolean;
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
      {renewed ? <p>{t`De oude link werkt niet meer.`}</p> : null}
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
  renewed = false,
}: {
  created: CreatedInvitation | null;
  onClose: () => void;
  renewed?: boolean;
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
          {created ? (
            <InvitationLink created={created} onClose={onClose} renewed={renewed} />
          ) : null}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

/** The person an invitation is for when it is made from Mensen: fixed, not chosen. */
export type InvitedPerson = { id: string; name: string; email: string };

/** The address field of an invitation for one person: in sight, prefilled, and required when
 * private letters are on the person's name (only that address may then accept). */
function PersonEmailField({
  workspaceId,
  person,
  email,
  onChange,
  serverSentence,
}: {
  workspaceId: string;
  person: InvitedPerson;
  email: string;
  onChange: (value: string) => void;
  serverSentence: string | undefined;
}) {
  const privatePost = useQuery(personPrivatePostQuery(workspaceId, person.id));
  const required = privatePost.data?.has_private_post === true;
  return (
    <TextField
      type="email"
      className="field"
      value={email}
      onChange={onChange}
      isRequired={required}
      isInvalid={serverSentence !== undefined}
      validationBehavior="aria"
    >
      <Label>{t`E-mailadres`}</Label>
      <Text slot="description">
        {required
          ? t`Op naam van ${person.name} staan privébrieven. Alleen dit adres kan de uitnodiging gebruiken.`
          : t`Alleen dit adres kan de uitnodiging gebruiken.`}
      </Text>
      <Input autoComplete="off" />
      <FieldError>{serverSentence}</FieldError>
    </TextField>
  );
}

function InviteForm({
  workspaceId,
  person,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  /** Fixed by the caller (Mensen): no choice of person, the address prefilled. */
  person?: InvitedPerson;
  onCreated: (created: CreatedInvitation) => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: persons } = useQuery({ ...personsQuery(workspaceId), enabled: !person });
  const [personId, setPersonId] = useState(person?.id ?? "");
  const [role, setRole] = useState<Role>("member");
  const [days, setDays] = useState<ExpiryDays>(7);
  const [email, setEmail] = useState(person?.email ?? "");
  const [note, setNote] = useState("");
  // Somebody who already has an account is no one to invite.
  // A department is nobody to invite: it has no account of its own.
  const free = (persons ?? []).filter(
    (person) => person.user_id === null && !person.archived && person.kind !== "department",
  );

  const sight = useSightChange(workspaceId);
  const create = useMutation({
    // The result goes to the caller's state; the mutation cache keeps nothing of it.
    gcTime: 0,
    mutationFn: async () => {
      const person = personId === "" ? null : personId;
      let made: CreatedInvitation | null = null;
      // An invitation that gives the newcomer more than a plain member invitation (the
      // admin role, or a person's post) asks through the preview first (S 4.4).
      await sight.tryFirst({
        preview: previewBody({
          member_change: "invite",
          member_role: role,
          member_person_id: person,
        }),
        confirmLabel: t`Ja, maak de uitnodiging`,
        write: async (confirmation) => {
          const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/invitations", {
            params: { path: { workspace_id: workspaceId } },
            body: {
              role,
              expires_in_days: days,
              email: email.trim(),
              person_id: person,
              note: note.trim(),
              ...confirmation,
            },
          });
          if (error !== undefined || !data) throw new ApiFailure(error);
          made = data;
        },
      });
      return made;
    },
    onSuccess: async (data) => {
      if (data === null) return;
      onCreated(data);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: invitationsQuery(workspaceId).queryKey }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "people"] }),
      ]);
    },
  });
  // The server's sentence when the address is missing goes to the address field itself.
  const emailSentence =
    create.error instanceof ApiFailure && create.error.code === "email_required"
      ? create.error.message
      : undefined;

  return (
    <>
      <Heading slot="title" className="dialog-title">
        {person ? t`${person.name} uitnodigen` : t`Iemand uitnodigen`}
      </Heading>
      <p>
        {person
          ? t`Je krijgt een link en een QR-code. Wie die opent, maakt een account of logt in en is daarna ${person.name} in deze werkruimte.`
          : t`Je krijgt een link en een QR-code. Wie die opent, maakt een account of logt in en hoort daarna bij deze werkruimte.`}
      </p>
      <Form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        {person ? (
          <PersonEmailField
            workspaceId={workspaceId}
            person={person}
            email={email}
            onChange={setEmail}
            serverSentence={emailSentence}
          />
        ) : null}
        {!person && free.length > 0 ? (
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
              {person ? null : (
                <TextField type="email" className="field" value={email} onChange={setEmail}>
                  <Label>{t`E-mailadres`}</Label>
                  <Text slot="description">{t`Alleen dit adres kan de uitnodiging gebruiken.`}</Text>
                  <Input autoComplete="off" />
                </TextField>
              )}
              <TextField className="field" value={note} onChange={setNote} maxLength={100}>
                <Label>{t`Notitie voor jezelf`}</Label>
                <Input autoComplete="off" />
              </TextField>
            </div>
          </DisclosurePanel>
        </Disclosure>
        <ErrorAlert error={emailSentence === undefined ? create.error : null} />
        <div className="dialog-actions">
          <Button className="button secondary" onPress={onCancel}>
            {t`Annuleren`}
          </Button>
          <Button type="submit" className="button" isPending={create.isPending}>
            {t`Link maken`}
          </Button>
        </div>
      </Form>
      {sight.dialog}
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
  person,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  /** Invite this person (Mensen): the person is fixed and its picker hidden. */
  person?: InvitedPerson;
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
        <Dialog
          className="dialog"
          aria-label={
            created ? t`Uitnodiging` : person ? t`${person.name} uitnodigen` : t`Iemand uitnodigen`
          }
        >
          {created ? (
            <InvitationLink created={created} onClose={close} />
          ) : (
            <InviteForm
              workspaceId={workspaceId}
              person={person}
              onCreated={setCreated}
              onCancel={close}
            />
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
