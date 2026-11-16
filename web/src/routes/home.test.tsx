import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  secondWorkspace,
  server,
  user,
  workspace,
} from "../test-utils";

/** Two workspaces: `/` shows the list instead of going straight to the post. */
function twoWorkspaces() {
  return http.get(`*${API}/workspaces`, () => HttpResponse.json([workspace, secondWorkspace]));
}

describe("home", () => {
  it("lists the workspaces in a table with kind and role, and links to the devices page", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/");
    expect(
      await screen.findByRole("heading", { level: 1, name: /werkruimtes/i }),
    ).toBeInTheDocument();
    const row = (await screen.findByRole("rowheader", { name: "Thuis" })).closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement).getAllByRole("cell");
    expect(cells.map((cell) => cell.textContent)).toEqual(["Thuis", "Beheerder"]);
    expect(screen.getByRole("columnheader", { name: "Rol" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Apparaten" })).toHaveAttribute("href", "/koppel-app");
  });

  it("marks the current page in the menu and shows who is logged in", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(within(nav).getByRole("link", { name: "Werkruimtes" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Apparaten" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(await screen.findByText("anna@example.nl")).toBeInTheDocument();
    expect(screen.getByText("Anna")).toBeInTheDocument();
  });

  it("links each workspace when there are several", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/");
    expect(await screen.findByRole("link", { name: "Bedrijf" })).toHaveAttribute(
      "href",
      `/w/${secondWorkspace.id}/post`,
    );
  });

  it("redirects to the only workspace", async () => {
    server.use(...loggedInHandlers());
    const { router } = renderApp("/");
    await waitFor(() => expect(router.state.location.pathname).toBe(`/w/${workspace.id}/post`));
  });

  it("logs out with the CSRF token and returns to the login screen", async () => {
    server.use(...loggedInHandlers());
    document.cookie = "csrftoken=tok123; path=/";
    let csrf: string | null = null;
    server.use(
      http.post(`*${API}/auth/logout`, ({ request }) => {
        csrf = request.headers.get("x-csrftoken");
        server.use(
          http.get(`*${API}/me`, () =>
            HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), {
              status: 401,
            }),
          ),
        );
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    const { router } = renderApp("/");
    await u.click(await screen.findByRole("button", { name: /uitloggen/i }));
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeInTheDocument();
    expect(csrf).toBe("tok123");
    expect(router.state.location.pathname).toBe("/login");
  });

  it("goes to the login screen when the session ended while the page was open", async () => {
    const notLoggedIn = () =>
      HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 });
    let asked = 0;
    server.use(
      // The guard still sees a user; once the page asks for data the session is gone.
      http.get(`*${API}/me`, () => {
        asked += 1;
        return asked === 1 ? HttpResponse.json(user) : notLoggedIn();
      }),
      http.get(`*${API}/workspaces`, notLoggedIn),
      ...loggedInHandlers(),
    );
    const { router } = renderApp("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });
});
