import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button, Form, Label, Radio, RadioGroup } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery } from "../api/queries";
import type { components } from "../api/schema";
import { Icon } from "../components/icons";
import { ErrorAlert, Field, Split, usePageTitle } from "../components/page";
import { requireVisitor } from "../guards";
import { rootRoute } from "./root";

type SetupBody = components["schemas"]["SetupIn"];

function SetupPage() {
  usePageTitle(t`Brievie instellen`);
  const [kind, setKind] = useState("");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const setup = useMutation({
    mutationFn: async (body: SetupBody) => {
      const { data, error } = await api.POST("/api/v1/setup", { body });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (result) => {
      // A rule asks for two-step verification first: signing in continues there.
      if ("step" in result) {
        await navigate({ to: "/login" });
        return;
      }
      // Setting up logs you in.
      queryClient.setQueryData(meQuery.queryKey, result.user);
      // The new workspace says how it is set up, with what to do next (S 4.2).
      await navigate({
        to: "/w/$workspaceId/ingesteld",
        params: { workspaceId: result.workspace.id },
      });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const business = form.get("workspace_kind") === "business";
    setup.mutate({
      workspace_kind: business ? "business" : "home",
      workspace_name: String(form.get("workspace_name")),
      name: String(form.get("name")),
      email: String(form.get("email")),
      password: String(form.get("password")),
      setup_token: String(form.get("setup_token")).trim(),
      // Departments are made on Afdelingen after the start, not as labels (S 4.2).
      extra_labels: [],
    });
  }

  return (
    <Split wide>
      <div className="heading">
        <h1>{t`Welkom bij Brievie`}</h1>
        <p className="lede">{t`Stel je eigen Brievie-server in. Dit doe je maar één keer.`}</p>
      </div>
      <Form onSubmit={submit} className="form">
        <RadioGroup
          name="workspace_kind"
          isRequired
          className="choice"
          value={kind}
          onChange={setKind}
        >
          <Label className="choice-label">{t`Waar gebruik je Brievie voor?`}</Label>
          <div className="cards">
            <Radio value="home" className="card">
              <span className="card-top" aria-hidden="true">
                <Icon name="home" className="card-icon" />
                <span className="radio-indicator" />
              </span>
              <strong>{t`Thuis`}</strong>
              <span>{t`Voor jezelf en je gezin. Iedereen ziet alle post, behalve wat privé is.`}</span>
            </Radio>
            <Radio value="business" className="card">
              <span className="card-top" aria-hidden="true">
                <Icon name="building" className="card-icon" />
                <span className="radio-indicator" />
              </span>
              <strong>{t`Organisatie`}</strong>
              <span>{t`Voor een bedrijf, praktijk of vereniging. Ieder ziet zijn eigen post en die van zijn afdeling.`}</span>
            </Radio>
          </div>
        </RadioGroup>
        <Field
          label={t`Naam, bijvoorbeeld 'Familie de Vries' of 'Bakkerij Jansen'`}
          name="workspace_name"
          maxLength={150}
        />
        <h2 className="form-section">{t`Jouw account`}</h2>
        <Field label={t`Je naam`} name="name" autoComplete="name" maxLength={150} />
        <Field label={t`E-mailadres`} name="email" type="email" autoComplete="username" />
        <Field
          label={t`Wachtwoord`}
          description={t`Kies minstens 12 tekens.`}
          name="password"
          type="password"
          autoComplete="new-password"
        />
        <Field
          label={t`Installatiecode`}
          description={t`Je vindt de installatiecode in het logboek van de Brievie-container.`}
          name="setup_token"
          autoComplete="off"
          literal
        />
        <ErrorAlert error={setup.error} />
        <Button type="submit" className="button" isPending={setup.isPending}>
          {t`Start Brievie`}
        </Button>
      </Form>
    </Split>
  );
}

export const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/setup",
  beforeLoad: ({ context }) => requireVisitor(context.queryClient, "setup"),
  component: SetupPage,
});
