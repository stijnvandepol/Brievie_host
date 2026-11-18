import { t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button, Form, Input, Label, Text, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { MessageBar } from "../components/ui";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

function PasswordPage() {
  usePageTitle(t`Wachtwoord wijzigen`);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const change = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/v1/me/password", {
        body: { current_password: current, new_password: next },
      });
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setAgain("");
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    change.reset();
    if (next !== again) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate();
  }

  return (
    <Shell>
      <PageHeader
        title={t`Wachtwoord wijzigen`}
        description={t`Na het wijzigen word je in je andere browsers uitgelogd. Je gekoppelde telefoon blijft werken.`}
      />
      <Form onSubmit={submit} className="form form-narrow">
        <TextField
          type="password"
          className="field"
          value={current}
          onChange={setCurrent}
          isRequired
        >
          <Label>{t`Huidig wachtwoord`}</Label>
          <Input autoComplete="current-password" />
        </TextField>
        <TextField type="password" className="field" value={next} onChange={setNext} isRequired>
          <Label>{t`Nieuw wachtwoord`}</Label>
          <Text slot="description">{t`Kies minstens 12 tekens.`}</Text>
          <Input autoComplete="new-password" />
        </TextField>
        <TextField type="password" className="field" value={again} onChange={setAgain} isRequired>
          <Label>{t`Herhaal nieuw wachtwoord`}</Label>
          <Input autoComplete="new-password" />
        </TextField>
        {mismatch ? (
          <MessageBar intent="error" role="alert">
            {t`De twee nieuwe wachtwoorden zijn niet hetzelfde.`}
          </MessageBar>
        ) : null}
        <ErrorAlert error={change.error} />
        {change.isSuccess ? (
          <MessageBar intent="success" role="status">
            {t`Je wachtwoord is gewijzigd. Je andere sessies zijn beëindigd.`}
          </MessageBar>
        ) : null}
        <div>
          <Button type="submit" className="button" isPending={change.isPending}>
            {t`Wachtwoord wijzigen`}
          </Button>
        </div>
      </Form>
    </Shell>
  );
}

export const passwordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/account/wachtwoord",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: PasswordPage,
});
