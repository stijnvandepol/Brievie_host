import { t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";
import { Icon, type IconName } from "./icons";
import { ErrorAlert } from "./page";

/**
 * A question before something that cannot be taken back: one sentence that names what
 * follows. The dialog stays open while the call runs and when it fails, with the server's
 * own sentence, so a refusal ("you are the last admin") is read where the question was asked.
 */
export function ConfirmDialog({
  isOpen,
  onOpenChange,
  title,
  children,
  confirmLabel,
  onConfirm,
  closeOnSuccess = true,
  tone = "danger",
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<unknown>;
  /** False when the caller closes the question itself (to show what it led to). */
  closeOnSuccess?: boolean;
  /** `danger` is a red button for something that cannot be taken back; `primary` for a plain yes. */
  tone?: "danger" | "primary";
}) {
  const run = useMutation({
    mutationFn: onConfirm,
    onSuccess: () => {
      if (closeOnSuccess) onOpenChange(false);
    },
  });
  const { reset } = run;
  useEffect(() => {
    if (!isOpen) reset();
  }, [isOpen, reset]);
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="overlay">
      <Modal className="modal">
        <Dialog role="alertdialog" className="dialog">
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          {children}
          <ErrorAlert error={run.error} />
          <div className="dialog-actions">
            <Button autoFocus className="button secondary" onPress={() => onOpenChange(false)}>
              {t`Annuleren`}
            </Button>
            <Button
              className={tone === "danger" ? "button danger-solid" : "button"}
              isPending={run.isPending}
              onPress={() => run.mutate()}
            >
              {confirmLabel}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

/** A quiet red button that opens a `ConfirmDialog`. `label` is what the button says; the
 * accessible name adds whom it is about when `ariaLabel` is given. */
export function ConfirmAction({
  label,
  ariaLabel,
  icon,
  ...dialog
}: {
  label: string;
  ariaLabel?: string;
  icon: IconName;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<unknown>;
  tone?: "danger" | "primary";
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        className={dialog.tone === "primary" ? "button-text" : "button-text danger"}
        aria-label={ariaLabel}
        onPress={() => setOpen(true)}
      >
        <Icon name={icon} />
        {label}
      </Button>
      <ConfirmDialog isOpen={open} onOpenChange={setOpen} {...dialog} />
    </>
  );
}
