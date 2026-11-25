import { createRoute, redirect } from "@tanstack/react-router";
import { validateArchiveSearch } from "./archive";
import { workspaceRoute } from "./workspace";

/**
 * "Alle post" became "Archief" (archive spec S 5, Besluit B1): the old address keeps working
 * for bookmarks and goes to Archief with the filters both understand (`persoon`, `label`,
 * the status). A letter's own page keeps its path `/post/<id>`.
 */
export const postRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "post",
  validateSearch: validateArchiveSearch,
  beforeLoad: ({ params, search }) => {
    throw redirect({ to: "/w/$workspaceId/archief", params, search, replace: true });
  },
});
