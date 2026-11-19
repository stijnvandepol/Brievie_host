import { t } from "@lingui/core/macro";
import { useEffect, useRef, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { ConfirmDialog } from "./confirm-dialog";
import { OneTimeLink } from "./one-time-link";

type Reset = components["schemas"]["PasswordResetOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "long", timeStyle: "short" });

function ResetLink({ name, reset, onClose }: { name: string; reset: Reset; onClose: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const expires = dateTime.format(new Date(reset.expires_at));
  return (
    <>
      <Heading slot="title" className="dialog-title" ref={heading} tabIndex={-1}>
        {t`Herstellink voor ${name}`}
      </Heading>
      <p>{t`Geef deze link aan ${name}. Hij werkt 24 uur en één keer.`}</p>
      <OneTimeLink link={reset.link} />
      <p className="muted small">{t`Je ziet de link maar één keer. Geldig tot ${expires}.`}</p>
      <div className="dialog-actions">
        <Button className="button" onPress={onClose}>
          {t`Klaar`}
        </Button>
      </div>
    </>
  );
}

/**
 * A password-reset link for a member, in two steps: first what it means, then the link,
 * once. Closing the dialog forgets the link; nothing keeps it in a cache or a list.
 */
export function ResetLinkDialog({
  userId,
  name,
  isOpen,
  onOpenChange,
}: {
  userId: string;
  name: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [created, setCreated] = useState<Reset | null>(null);
  useEffect(() => {
    if (!isOpen) setCreated(null);
  }, [isOpen]);
  async function make() {
    const { data, error } = await api.POST("/api/v1/users/{user_id}/password-reset", {
      params: { path: { user_id: userId } },
    });
    if (error !== undefined || !data) throw new ApiFailure(error);
    // Straight into this dialog's state: no query or mutation keeps the link.
    setCreated(data);
  }
  return (
    <>
      <ConfirmDialog
        isOpen={isOpen && created === null}
        onOpenChange={onOpenChange}
        title={t`Herstellink maken voor ${name}?`}
        confirmLabel={t`Link maken`}
        closeOnSuccess={false}
        onConfirm={make}
      >
        <p>
          {t`Met de link kiest ${name} een nieuw wachtwoord. Daarna moet ${name} opnieuw inloggen en de telefoon opnieuw koppelen. Een eerdere herstellink van ${name} werkt daarna niet meer.`}
        </p>
      </ConfirmDialog>
      <ModalOverlay
        isOpen={isOpen && created !== null}
        onOpenChange={onOpenChange}
        className="overlay"
      >
        <Modal className="modal">
          <Dialog className="dialog" aria-label={t`Herstellink voor ${name}`}>
            {created ? (
              <ResetLink name={name} reset={created} onClose={() => onOpenChange(false)} />
            ) : null}
          </Dialog>
        </Modal>
      </ModalOverlay>
    </>
  );
}
