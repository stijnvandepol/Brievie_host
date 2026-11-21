import { i18n } from "@lingui/core";
import { messages as en } from "./locales/en/messages.po";
import { messages as nl } from "./locales/nl/messages.po";

// Dutch is the source language (the extracted msgids) and the default; English is the
// second catalogue. Both are small, so both are loaded up front.
export const LOCALES = ["nl", "en"] as const;
export type Locale = (typeof LOCALES)[number];

// Each language in its own name, so a reader finds theirs; never translated.
export const LANGUAGE_NAMES: Record<Locale, string> = { nl: "Nederlands", en: "English" };

const STORAGE_KEY = "brievie.language";

export function isLocale(value: unknown): value is Locale {
  return value === "nl" || value === "en";
}

function stored(): Locale | undefined {
  try {
    const value = globalThis.localStorage?.getItem(STORAGE_KEY);
    return isLocale(value) ? value : undefined;
  } catch {
    return undefined;
  }
}

function remember(locale: Locale) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, locale);
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
}

/** The browser's first language we have, else Dutch. */
function fromBrowser(): Locale {
  const wanted = globalThis.navigator?.languages ?? [globalThis.navigator?.language ?? ""];
  for (const tag of wanted) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLocale(base)) return base;
  }
  return "nl";
}

/** The locale this browser starts in: a choice made here before, else the browser's. */
export function initialLocale(): Locale {
  return stored() ?? fromBrowser();
}

/**
 * Switch the interface (and `<html lang>`) to `locale`, and remember it in this browser
 * so the sign-in page opens in it too. The server follows through Accept-Language until
 * the account has its own choice.
 */
export function setLocale(locale: Locale) {
  remember(locale);
  if (i18n.locale === locale) return;
  i18n.activate(locale);
  document.documentElement.lang = locale;
}

/** The current locale. */
export function currentLocale(): Locale {
  return isLocale(i18n.locale) ? i18n.locale : "nl";
}

/**
 * The BCP 47 tag for dates and numbers: Dutch as in the Netherlands; English in the
 * browser's own English region when it has one (en-US, en-IE, ...), else en-GB.
 */
export function formattingLocale(): string {
  if (currentLocale() === "nl") return "nl-NL";
  const english = (globalThis.navigator?.languages ?? []).find((tag) =>
    tag.toLowerCase().startsWith("en-"),
  );
  return english ?? "en-GB";
}

i18n.load({ nl, en });
const first = initialLocale();
i18n.activate(first);
document.documentElement.lang = first;

export { i18n };
