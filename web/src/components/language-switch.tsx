import { t } from "@lingui/core/macro";
import { useLingui } from "@lingui/react";
import { currentLocale, LANGUAGE_NAMES, LOCALES, setLocale } from "../i18n";

/**
 * The language switch of the screens before sign-in (setup, sign-in, invitation, reset).
 * The choice is kept in this browser; once there is an account, its own choice wins.
 */
export function LanguageSwitch() {
  // Renders again when the language changes.
  useLingui();
  const active = currentLocale();
  return (
    <fieldset className="language-switch">
      <legend className="visually-hidden">{t`Taal`}</legend>
      {LOCALES.map((locale) => (
        <button
          key={locale}
          type="button"
          lang={locale}
          className="language-option"
          aria-pressed={locale === active}
          onClick={() => setLocale(locale)}
        >
          {LANGUAGE_NAMES[locale]}
        </button>
      ))}
    </fieldset>
  );
}
