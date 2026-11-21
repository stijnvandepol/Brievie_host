import type { QueryClient } from "@tanstack/react-query";
import { redirect } from "@tanstack/react-router";
import { meQuery, setupStatusQuery } from "./api/queries";
import { resetSearch } from "./search-term";
import { clearUndo } from "./undo-trash";

// Route guards always ask the server (staleTime 0): the answers change when somebody
// sets up, logs in or logs out.
const asked = { staleTime: 0 };

/** A protected page: set up first, then log in. Returns the logged-in user. */
export async function requireUser(queryClient: QueryClient) {
  const status = await queryClient.fetchQuery({ ...setupStatusQuery, ...asked });
  if (status.needs_setup) throw redirect({ to: "/setup" });
  const user = await queryClient.fetchQuery({ ...meQuery, ...asked });
  if (!user) {
    // The session ended: whoever signs in next does not get the last search.
    resetSearch();
    clearUndo();
    throw redirect({ to: "/login" });
  }
  return user;
}

/** Setup and login screens: skipped when they no longer apply. */
export async function requireVisitor(queryClient: QueryClient, screen: "setup" | "login") {
  const status = await queryClient.fetchQuery({ ...setupStatusQuery, ...asked });
  if (status.needs_setup && screen === "login") throw redirect({ to: "/setup" });
  if (!status.needs_setup && screen === "setup") throw redirect({ to: "/login" });
  const user = await queryClient.fetchQuery({ ...meQuery, ...asked });
  if (user) throw redirect({ to: "/" });
}
