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
  server,
  user,
} from "../test-utils";

const here = {
  id: "c1111111-1111-4111-8111-111111111111",
  method: "password",
  started_at: "2026-11-17T09:00:00Z",
  last_used_at: "2026-11-18T08:00:00Z",
  browser: "Firefox op macOS",
  client_ip: "192.168.1.20",
  current: true,
};
const other = {
  ...here,
  id: "c2222222-2222-4222-8222-222222222222",
  browser: "Safari op iPad",
  client_ip: "192.168.1.31",
  current: false,
};

describe("my sessions", () => {
  it("lists where I am signed in and marks this browser", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/me/sessions`, () => HttpResponse.json([here, other])),
    );
    renderApp("/account/sessies");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Waar je bent ingelogd" }),
    ).toBeInTheDocument();
    const mine = (await screen.findByRole("rowheader", { name: /Firefox op macOS/ })).closest(
      "tr",
    ) as HTMLElement;
    expect(within(mine).getByText("Deze browser")).toBeInTheDocument();
    expect(within(mine).getByText("192.168.1.20")).toBeInTheDocument();
    expect(within(mine).getByText("Wachtwoord")).toBeInTheDocument();
    for (const name of ["Browser", "Methode", "Adres", "Gestart", "Laatst gebruikt"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const theirs = screen
      .getByRole("rowheader", { name: /Safari op iPad/ })
      .closest("tr") as HTMLElement;
    expect(within(theirs).queryByText("Deze browser")).not.toBeInTheDocument();
  });

  it("ends another session after asking, and the row disappears", async () => {
    let list = [here, other];
    let ended = "";
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/me/sessions`, () => HttpResponse.json(list)),
      http.delete(`*${API}/me/sessions/:id`, ({ request }) => {
        ended = new URL(request.url).pathname.split("/").at(-1) ?? "";
        list = list.filter((session) => session.id !== ended);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/account/sessies");
    await u.click(await screen.findByRole("button", { name: "Afmelden: Safari op iPad" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Afmelden?" });
    expect(dialog).toHaveTextContent("Safari op iPad is daarna uitgelogd.");
    expect(ended).toBe("");
    await u.click(within(dialog).getByRole("button", { name: "Afmelden" }));
    await waitFor(() =>
      expect(screen.queryByRole("rowheader", { name: /Safari/ })).not.toBeInTheDocument(),
    );
    expect(ended).toBe(other.id);
    expect(screen.getByRole("rowheader", { name: /Firefox/ })).toBeInTheDocument();
  });

  it("ending this browser's session goes to the sign-in page", async () => {
    let signedIn = true;
    server.use(
      http.get(`*${API}/me`, () =>
        signedIn
          ? HttpResponse.json(user)
          : HttpResponse.json(errorBody("not_authenticated", "Log eerst in."), { status: 401 }),
      ),
      ...loggedInHandlers(),
      http.get(`*${API}/me/sessions`, () => HttpResponse.json(signedIn ? [here, other] : [])),
      http.delete(`*${API}/me/sessions/:id`, () => {
        signedIn = false;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/account/sessies");
    await u.click(await screen.findByRole("button", { name: "Afmelden: Firefox op macOS" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Je wordt hier uitgelogd");
    await u.click(within(dialog).getByRole("button", { name: "Afmelden" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inloggen" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the server's sentence when ending fails", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/me/sessions`, () => HttpResponse.json([here, other])),
      http.delete(`*${API}/me/sessions/:id`, () =>
        HttpResponse.json(errorBody("session_not_found", "Deze sessie bestaat niet meer."), {
          status: 404,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/account/sessies");
    await u.click(await screen.findByRole("button", { name: "Afmelden: Safari op iPad" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Afmelden" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Deze sessie bestaat niet meer.",
    );
  });

  it("is reachable from the account menu", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/me/sessions`, () => HttpResponse.json([here])),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).getByRole("link", { name: "Apparaten" })).toHaveAttribute(
      "href",
      "/koppel-app",
    );
    expect(within(menu).getByRole("link", { name: "Wachtwoord wijzigen" })).toHaveAttribute(
      "href",
      "/account/wachtwoord",
    );
    await u.click(within(menu).getByRole("link", { name: "Waar je bent ingelogd" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Waar je bent ingelogd" }),
    ).toBeInTheDocument();
  });
});
