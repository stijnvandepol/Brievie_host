import { t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { type FormEvent, useState } from "react";
import {
  Button,
  Dialog,
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
import { saveFile } from "../save-file";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";

/** The six digits from an authenticator app: a phone shows the number pad and offers the
 * code it just received. */
export function CodeField({
  value,
  onChange,
  label,
  description,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  description?: string;
}) {
  return (
    <TextField className="field" value={value} onChange={onChange} isRequired maxLength={8}>
      <Label>{label}</Label>
      {description ? <Text slot="description">{description}</Text> : null}
      <Input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]*" />
    </TextField>
  );
}

/** Save the recovery codes as a text file, made in the browser (no server round trip). */
export function downloadRecoveryCodes(codes: string[]) {
  const text = [
    t`Herstelcodes voor Brievie (${window.location.host})`,
    "",
    ...codes,
    "",
    t`Elke code werkt één keer.`,
    "",
  ].join("\n");
  saveFile(new Blob([text], { type: "text/plain;charset=utf-8" }), "brievie-herstelcodes.txt");
}

/**
 * The recovery codes, shown once. The dialog cannot be closed (Escape, outside) until the
 * reader says the codes are saved: without them a lost phone locks the account.
 */
export function RecoveryCodesDialog({ codes, onSaved }: { codes: string[]; onSaved: () => void }) {
  return (
    <ModalOverlay isOpen isDismissable={false} isKeyboardDismissDisabled className="overlay">
      <Modal className="modal">
        <Dialog className="dialog">
          <Heading slot="title" className="dialog-title">
            {t`Je herstelcodes`}
          </Heading>
          <p>
            {t`Bewaar deze herstelcodes op papier of in je wachtwoordmanager. Elke code werkt één keer. Je ziet ze maar één keer.`}
          </p>
          <ul className="recovery-codes" aria-label={t`Herstelcodes`}>
            {codes.map((code) => (
              <li key={code} className="num code">
                {code}
              </li>
            ))}
          </ul>
          <div className="dialog-actions">
            <Button className="button secondary" onPress={() => downloadRecoveryCodes(codes)}>
              <Icon name="save" />
              {t`Downloaden`}
            </Button>
            <Button className="button" onPress={onSaved}>
              {t`Ik heb ze bewaard`}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

/** The three steps of the set-up, the QR code and the key as text. */
export function ScanSteps({ secret }: { secret: { secret: string; otpauth_uri: string } }) {
  return (
    <>
      <ol className="steps">
        <li>
          {t`Open een authenticator-app op je telefoon, zoals Microsoft Authenticator, Google Authenticator of de app Wachtwoorden op je iPhone.`}
        </li>
        <li>{t`Scan deze code.`}</li>
        <li>{t`Typ de 6 cijfers die de app laat zien.`}</li>
      </ol>
      <div className="setup-qr">
        <QRCodeSVG
          value={secret.otpauth_uri}
          title={t`QR-code voor je authenticator-app`}
          size={200}
          marginSize={4}
          level="M"
        />
      </div>
      <p className="muted">{t`Scannen lukt niet? Typ deze sleutel in de app:`}</p>
      <p className="num code secret">{secret.secret}</p>
    </>
  );
}

/**
 * Setting up two-step verification (spec S 9): the three steps, the QR code and the key as
 * text, the code from the app, then the recovery codes. `pending`: a sign-in that must set
 * it up first; confirming signs in. From a session, an older sign-in first types the
 * password again.
 */
export function SecondFactorSetup({
  pending = false,
  onDone,
}: {
  pending?: boolean;
  onDone: () => void | Promise<void>;
}) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const begin = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/me/second-factor/setup");
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  const reauthenticate = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/v1/auth/reauthenticate", {
        body: { password, code: "" },
      });
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: () => {
      setPassword("");
      begin.mutate();
    },
  });
  const confirm = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/me/second-factor/setup/confirm", {
        body: { code: code.replace(/\s/g, "") },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data.recovery_codes;
    },
  });
  const needsPassword =
    !pending &&
    begin.error instanceof ApiFailure &&
    begin.error.code === "reauthentication_required";

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    reauthenticate.mutate();
  }

  function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate();
  }

  if (confirm.data) {
    return <RecoveryCodesDialog codes={confirm.data} onSaved={() => void onDone()} />;
  }

  if (needsPassword) {
    return (
      <Form onSubmit={submitPassword} className="form form-narrow">
        <TextField
          type="password"
          className="field"
          value={password}
          onChange={setPassword}
          isRequired
        >
          <Label>{t`Wachtwoord`}</Label>
          <Text slot="description">{t`Vul je wachtwoord in voordat je tweestapsverificatie aanzet. Zo weten we dat jij het bent.`}</Text>
          <Input autoComplete="current-password" />
        </TextField>
        <ErrorAlert error={reauthenticate.error} />
        <div>
          <Button type="submit" className="button" isPending={reauthenticate.isPending}>
            {t`Bevestigen`}
          </Button>
        </div>
      </Form>
    );
  }

  const secret = begin.data;
  if (!secret) {
    return (
      <div className="form form-narrow">
        <ErrorAlert error={begin.error} />
        <div>
          <Button className="button" onPress={() => begin.mutate()} isPending={begin.isPending}>
            <Icon name="shield" />
            {t`Tweestapsverificatie aanzetten`}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <section className="second-factor-setup" aria-labelledby="second-factor-setup-title">
      <h2 id="second-factor-setup-title">{t`Tweestapsverificatie aanzetten`}</h2>
      <ScanSteps secret={secret} />
      <Form onSubmit={submitCode} className="form form-narrow">
        <CodeField value={code} onChange={setCode} label={t`Code uit de app`} />
        <ErrorAlert error={confirm.error} />
        <div>
          <Button type="submit" className="button" isPending={confirm.isPending}>
            {t`Aanzetten`}
          </Button>
        </div>
      </Form>
    </section>
  );
}
