import { t } from "@lingui/core/macro";
import { useState } from "react";
import { Button, Input, Label, TextField } from "react-aria-components";
import { Icon } from "./icons";

type CopyState = "idle" | "copied" | "failed";

/**
 * A link that is shown once (an invitation, a password reset): in a read-only field, with a
 * button to copy it. Copying is the user's own action and is confirmed in a polite live
 * region. The link lives only in the props of whoever shows this; nothing here keeps it.
 */
export function OneTimeLink({
  link,
  label = t`Link`,
  copyLabel = t`Kopieer link`,
}: {
  link: string;
  label?: string;
  copyLabel?: string;
}) {
  const [copy, setCopy] = useState<CopyState>("idle");

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(link);
      setCopy("copied");
    } catch {
      // No Clipboard API, or the browser said no: the field is there to copy by hand.
      setCopy("failed");
    }
  }

  let status = "";
  if (copy === "copied") status = t`Gekopieerd`;
  else if (copy === "failed") status = t`Kopiëren lukt niet. Kopieer de link hierboven zelf.`;

  return (
    <div className="one-time-link">
      <TextField isReadOnly className="field">
        <Label>{label}</Label>
        <Input value={link} onFocus={(event) => event.currentTarget.select()} />
      </TextField>
      <div>
        <Button className="button secondary" onPress={copyLink}>
          <Icon name="copy" />
          {copyLabel}
        </Button>
      </div>
      <p role="status" aria-live="polite" className="status">
        {status}
      </p>
    </div>
  );
}
