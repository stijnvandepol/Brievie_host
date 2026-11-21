import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { Label, Radio, RadioGroup } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery } from "../api/queries";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { requireUser } from "../guards";
import { isLocale, LANGUAGE_NAMES, LOCALES, setLocale } from "../i18n";
import { rootRoute } from "./root";

// "" is no choice: the browser's (or the phone's) language decides.
type Choice = "" | "nl" | "en";

function isChoice(value: unknown): value is Choice {
  return value === "" || isLocale(value);
}

function LanguagePage() {
  usePageTitle(t`Taal`);
  const queryClient = useQueryClient();
  const { data: me } = useQuery(meQuery);
  const save = useMutation({
    mutationFn: async (language: Choice) => {
      const { data, error } = await api.PUT("/api/v1/me/language", { body: { language } });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (user) => {
      queryClient.setQueryData(meQuery.queryKey, user);
      // Switching mounts the pages again, in the new language.
      if (isLocale(user.language)) setLocale(user.language);
    },
  });
  const value: Choice = isChoice(me?.language) ? me.language : "";

  return (
    <Shell>
      <PageHeader
        title={t`Taal`}
        description={t`De taal van Brievie op deze server en in de app. De inhoud van je brieven blijft zoals hij is.`}
      />
      <RadioGroup
        className="choice form-narrow"
        value={value}
        isDisabled={save.isPending || !me}
        onChange={(next) => {
          if (isChoice(next)) save.mutate(next);
        }}
      >
        <Label className="choice-label">{t`Taal van Brievie`}</Label>
        <div className="cards cards-stack">
          <Radio value="" className="card" aria-label={t`Zoals je browser of telefoon`}>
            <span className="card-top" aria-hidden="true">
              <strong>{t`Zoals je browser of telefoon`}</strong>
              <span className="radio-indicator" />
            </span>
            <span>{t`Elk apparaat gebruikt zijn eigen taal.`}</span>
          </Radio>
          {LOCALES.map((locale) => (
            <Radio key={locale} value={locale} className="card" aria-label={LANGUAGE_NAMES[locale]}>
              <span className="card-top" aria-hidden="true">
                <strong lang={locale}>{LANGUAGE_NAMES[locale]}</strong>
                <span className="radio-indicator" />
              </span>
              <span>{t`Overal, ook in de app.`}</span>
            </Radio>
          ))}
        </div>
      </RadioGroup>
      <ErrorAlert error={save.error} />
    </Shell>
  );
}

export const languageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/account/taal",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: LanguagePage,
});
