import { formattingLocale } from "./i18n";

/** A date formatter that follows the active language (looked up on every call). */
export interface DateFormat {
  format(value: Date): string;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

export function dateFormat(options: Intl.DateTimeFormatOptions): DateFormat {
  return {
    format(value) {
      const locale = formattingLocale();
      const key = `${locale} ${JSON.stringify(options)}`;
      let formatter = formatters.get(key);
      if (!formatter) {
        formatter = new Intl.DateTimeFormat(locale, options);
        formatters.set(key, formatter);
      }
      return formatter.format(value);
    },
  };
}
