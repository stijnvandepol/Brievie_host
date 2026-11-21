import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import {
  API,
  documentHandlers,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  twoWorkspaces,
  user,
  workspace,
} from "../test-utils";

const TOKEN = "reset-token-xyz";
const DEAD = "Deze herstellink werkt niet meer. Vraag een nieuwe.";

afterEach(() => window.history.replaceState(null, "", "/"));

async function choose(u: ReturnType<typeof userEvent.setup>, again = "nieuw wachtwoord 22") {
  await u.type(await screen.findByLabelText("Nieuw wachtwoord"), "nieuw wachtwoord 22");
  await u.type(screen.getByLabelText("Herhaal nieuw wachtwoord"), again);
  await u.click(screen.getByRole("button", { name: "Wachtwoord opslaan" }));
}

describe("the reset page", () => {
  it("sends the token in the body and then goes to the start page", async () => {
    const seen: { url: string; body: unknown }[] = [];
    server.use(
      twoWorkspaces(),
      http.post(`*${API}/auth/password-reset`, async ({ request }) => {
        seen.push({ url: request.url, body: await request.clone().json() });
        return HttpResponse.json(user);
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    const { router } = renderApp(`/wachtwoord-herstellen#${TOKEN}`);
    await choose(u);
    expect(await screen.findByRole("heading", { level: 1, name: "Mijn post" })).toBeInTheDocument();
    expect(seen).toHaveLength(1);
    expect(seen[0]?.body).toEqual({ token: TOKEN, password: "nieuw wachtwoord 22" });
    expect(new URL(seen[0]?.url ?? "").search).toBe("");
    expect(seen[0]?.url).not.toContain(TOKEN);
    expect(router.state.location.pathname).toBe(`/w/${workspace.id}/mijn-post`);
  });

  it("removes the token from the address bar before anything is asked", async () => {
    window.history.replaceState(null, "", `/wachtwoord-herstellen#${TOKEN}`);
    const { router } = renderApp(`/wachtwoord-herstellen#${TOKEN}`);
    await screen.findByLabelText("Nieuw wachtwoord");
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(TOKEN);
    expect(router.state.location.href).not.toContain(TOKEN);
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain(
      TOKEN,
    );
  });

  it("does not send two different passwords", async () => {
    const u = userEvent.setup();
    renderApp(`/wachtwoord-herstellen#${TOKEN}`);
    await choose(u, "iets anders 33");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "De twee wachtwoorden zijn niet hetzelfde.",
    );
  });

  it("shows the server's sentence for a dead link and keeps the page", async () => {
    server.use(
      http.post(`*${API}/auth/password-reset`, () =>
        HttpResponse.json(errorBody("reset_link_invalid", DEAD), { status: 400 }),
      ),
    );
    const u = userEvent.setup();
    renderApp(`/wachtwoord-herstellen#${TOKEN}`);
    await choose(u);
    expect(await screen.findByRole("alert")).toHaveTextContent(DEAD);
  });

  it("keeps the token out of the mutation cache", async () => {
    server.use(
      http.post(`*${API}/auth/password-reset`, () =>
        HttpResponse.json(errorBody("reset_link_invalid", DEAD), { status: 400 }),
      ),
    );
    const u = userEvent.setup();
    const { queryClient } = renderApp(`/wachtwoord-herstellen#${TOKEN}`);
    await choose(u);
    await screen.findByRole("alert");
    const state = queryClient
      .getMutationCache()
      .getAll()
      .map((mutation) => JSON.stringify({ v: mutation.state.variables, d: mutation.state.data }));
    expect(state.join()).not.toContain(TOKEN);
  });

  it("without a token in the address it says so truthfully and shows no form", async () => {
    renderApp("/wachtwoord-herstellen");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Open de link uit je bericht opnieuw.");
    expect(alert).not.toHaveTextContent(DEAD);
    expect(screen.queryByLabelText("Nieuw wachtwoord")).not.toBeInTheDocument();
  });
});
