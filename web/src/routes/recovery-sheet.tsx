import { t } from "@lingui/core/macro";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createRoute, redirect, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button, Form, Input, Label, Text, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery } from "../api/queries";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { CodeField } from "../components/second-factor-setup";
import { MessageBar } from "../components/ui";
import { requireUser } from "../guards";
import { saveFile } from "../save-file";
import { rootRoute } from "./root";

const FILE_NAME = "brievie-herstelblad.html";

/**
 * "Herstelblad": the page of the master key, for server admins. The server asks for the
 * password again when the sign-in is older than 15 minutes, like for a new phone.
 */
function RecoverySheetPage() {
  usePageTitle(t`Herstelblad`);
  const navigate = useNavigate();
  const [done, setDone] = useState(false);
  const download = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/api/v1/server/recovery-sheet", {
        parseAs: "blob",
      });
      if (!response.ok || !(data instanceof Blob)) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (sheet) => {
      saveFile(sheet, FILE_NAME);
      setDone(true);
    },
    onError: async (error) => {
      setDone(false);
      if (error instanceof ApiFailure && error.code === "not_authenticated") {
        await navigate({ to: "/login" });
      }
    },
  });
  const needsPassword =
    download.error instanceof ApiFailure && download.error.code === "reauthentication_required";
  const [password, setPassword] = useState("");
  // With two-step verification on, typing the password again asks the code as well.
  const withCode = useQuery(meQuery).data?.has_second_factor === true;
  const [code, setCode] = useState("");
  const confirm = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/v1/auth/reauthenticate", {
        body: { password, code: withCode ? code.replace(/\s/g, "") : "" },
      });
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: () => {
      setPassword("");
      setCode("");
      download.mutate();
    },
  });

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate();
  }

  return (
    <Shell>
      <PageHeader
        title={t`Herstelblad`}
        description={t`Op het herstelblad staat de hoofdsleutel van deze server. Zonder die sleutel kun je je documenten niet terugzetten als de server kapotgaat.`}
      />
      <section className="panel panel-pad" aria-labelledby="sheet-care">
        <h2 id="sheet-care">{t`Zo ga je ermee om`}</h2>
        <ul className="sentence-list">
          <li>{t`Druk het blad af en bewaar het op een veilige plek, niet op deze server.`}</li>
          <li>{t`Laat niemand de sleutel zien. Wie de sleutel en een back-up heeft, kan je documenten lezen.`}</li>
          <li>{t`Verwijder het bestand daarna van je computer.`}</li>
        </ul>
      </section>

      {needsPassword ? (
        <Form onSubmit={submitPassword} className="form form-narrow">
          <TextField
            type="password"
            className="field"
            value={password}
            onChange={setPassword}
            isRequired
          >
            <Label>{t`Wachtwoord`}</Label>
            <Text slot="description">{t`Vul je wachtwoord in voordat je het herstelblad downloadt. Zo weten we dat jij het bent.`}</Text>
            <Input autoComplete="current-password" />
          </TextField>
          {withCode ? (
            <CodeField value={code} onChange={setCode} label={t`Code uit de app`} />
          ) : null}
          <ErrorAlert error={confirm.error} />
          <div>
            <Button type="submit" className="button" isPending={confirm.isPending}>
              {t`Bevestigen`}
            </Button>
          </div>
        </Form>
      ) : (
        <>
          <ErrorAlert error={download.error} />
          <div>
            <Button
              className="button"
              onPress={() => download.mutate()}
              isPending={download.isPending}
            >
              <Icon name="save" />
              {t`Herstelblad downloaden`}
            </Button>
          </div>
        </>
      )}

      <p role="status" aria-live="polite" className="status">
        {done ? t`Het herstelblad is gedownload.` : ""}
      </p>
      {done ? (
        <MessageBar intent="info">
          {t`Druk het af en bewaar het veilig. Het bestand bevat de sleutel: verwijder het daarna.`}
        </MessageBar>
      ) : null}
    </Shell>
  );
}

export const recoverySheetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/server/herstelblad",
  beforeLoad: async ({ context }) => {
    const user = await requireUser(context.queryClient);
    // Only a server admin gets the sheet; the server refuses everyone else too.
    if (!user.is_server_admin) throw redirect({ to: "/" });
  },
  component: RecoverySheetPage,
});
