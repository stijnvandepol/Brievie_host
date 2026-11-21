import { useLingui } from "@lingui/react";
import { type AnyRouter, RouterProvider } from "@tanstack/react-router";

/**
 * The router, mounted again when the language changes: texts made with the `t` macro are
 * read while rendering, so every page must render anew. The query cache stays.
 */
export function LocaleRouter({ router }: { router: AnyRouter }) {
  const { i18n } = useLingui();
  return <RouterProvider key={i18n.locale} router={router} />;
}
