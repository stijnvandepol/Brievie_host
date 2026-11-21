import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  errorBody,
  HttpResponse,
  http,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, title }: { value: string; title?: string }) => (
    <svg role="img" aria-label={title} data-testid="qr" data-value={value} />
  ),
}));

const TOKEN = "abc-token-123";
const DEAD = "Deze uitnodiging werkt niet meer. Vraag een nieuwe.";
type Info = components["schemas"]["InvitationInfoOut"];

const info: Info = {
  workspace_name: "Thuis",
  invited_by: "Anna",
  role: "member",
  email_fixed: false,
  person_name: "",
  ai: {
    mode: "off",
    has_api_key: false,
    auto_confirm: false,
    auto_organise: false,
    autopilot_allowed: true,
  },
};

function lookup(result: Info = info, seen?: (request: Request, body: { token: string }) => void) {
  return http.post(`*${API}/invitations/lookup`, async ({ request }) => {
    const body = (await request.clone().json()) as { token: string };
    seen?.(request, body);
    return HttpResponse.json(result);
  });
}

const accepted = { user, workspace };
const SERVER_LINK = "brievie://server#v=1&url=https%3A%2F%2Fbrievie.example.nl&pairing=pp-1";

function pairing(secondsValid = 600) {
  return http.post(`*${API}/devices/pairing-codes`, () =>
    HttpResponse.json(
      {
        code: "ABCD-2345",
        expires_at: new Date(Date.now() + secondsValid * 1000).toISOString(),
        qr_payload: '{"v":1,"urls":["https://brievie.example.nl"],"code":"ABCD-2345"}',
        server_link: SERVER_LINK,
      },
      { status: 201 },
    ),
  );
}

function coarsePointer(coarse: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: coarse && query.includes("pointer: coarse"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

async function fillNewAccount(u: ReturnType<typeof userEvent.setup>) {
  await u.type(await screen.findByRole("textbox", { name: "Naam" }), "Els");
  await u.type(screen.getByRole("textbox", { name: "E-mailadres" }), "els@example.nl");
  await u.type(screen.getByLabelText("Wachtwoord"), "een lang wachtwoord 1");
}

describe("accept an invitation", () => {
  it("sends the token in the body, never in the url", async () => {
    const seen: { url: string; token: string }[] = [];
    server.use(lookup(info, (request, body) => seen.push({ url: request.url, token: body.token })));
    renderApp(`/uitnodiging#${TOKEN}`);
    await screen.findByRole("heading", { level: 1, name: /Anna nodigt je uit voor Thuis/ });
    expect(seen).toHaveLength(1);
    expect(seen[0]?.token).toBe(TOKEN);
    expect(new URL(seen[0]?.url ?? "").search).toBe("");
    expect(seen[0]?.url).not.toContain(TOKEN);
  });

  it("removes the token from the address bar before the first request", async () => {
    window.history.replaceState(null, "", `/uitnodiging#${TOKEN}`);
    const atRequest: string[] = [];
    server.use(lookup(info, () => atRequest.push(window.location.href)));
    const { router } = renderApp(`/uitnodiging#${TOKEN}`);
    await screen.findByRole("heading", { level: 1 });
    expect(atRequest).toEqual([`${window.location.origin}/uitnodiging`]);
    expect(window.location.hash).toBe("");
    expect(router.state.location.hash).toBe("");
    expect(router.state.location.href).not.toContain(TOKEN);
  });

  it("keeps the token out of storage and the page text", async () => {
    server.use(lookup());
    renderApp(`/uitnodiging#${TOKEN}`);
    await screen.findByRole("heading", { level: 1 });
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(TOKEN);
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("shows the server's sentence for a dead invitation and nothing else", async () => {
    server.use(
      http.post(`*${API}/invitations/lookup`, () =>
        HttpResponse.json(errorBody("invitation_invalid", DEAD), { status: 400 }),
      ),
    );
    renderApp(`/uitnodiging#${TOKEN}`);
    expect(await screen.findByRole("alert")).toHaveTextContent(DEAD);
    // Only the language switch, no action.
    const languages = screen.getByRole("group", { name: "Taal" });
    expect(screen.queryAllByRole("button").filter((b) => !languages.contains(b))).toEqual([]);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Thuis/)).not.toBeInTheDocument();
  });

  it("without a token in the address it says so truthfully and asks nothing", async () => {
    // No handler: a request would fail the test.
    renderApp("/uitnodiging");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Open de link uit je bericht opnieuw.");
    expect(alert).not.toHaveTextContent(DEAD);
  });

  it("keeps the token out of the mutation cache", async () => {
    server.use(lookup());
    const { queryClient } = renderApp(`/uitnodiging#${TOKEN}`);
    await screen.findByRole("heading", { level: 1 });
    const state = queryClient
      .getMutationCache()
      .getAll()
      .map((mutation) => JSON.stringify({ v: mutation.state.variables, d: mutation.state.data }));
    expect(state.join()).not.toContain(TOKEN);
  });

  it("shows the AI line before the choice when AI is on", async () => {
    server.use(
      lookup({
        ...info,
        ai: {
          has_api_key: false,
          auto_confirm: false,
          auto_organise: false,
          autopilot_allowed: true,
          mode: "external",
          supplier: "mistral",
          host: "api.mistral.ai",
          since: "2026-11-17T14:39:59Z",
        },
      }),
    );
    renderApp(`/uitnodiging#${TOKEN}`);
    const line = await screen.findByText(/AI aan: tekst van brieven gaat naar/);
    const choice = screen.getByRole("radio", { name: /Nieuw account/ });
    expect(line.compareDocumentPosition(choice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows no AI line when AI is off", async () => {
    server.use(lookup());
    renderApp(`/uitnodiging#${TOKEN}`);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/AI aan|AI uit/)).not.toBeInTheDocument();
  });

  it("explains the role in a plain sentence", async () => {
    server.use(lookup({ ...info, role: "viewer" }));
    renderApp(`/uitnodiging#${TOKEN}`);
    expect(await screen.findByText(/meekijker/)).toBeInTheDocument();
    expect(screen.getByText(/alles lezen, maar niets wijzigen/)).toBeInTheDocument();
  });

  it("hints at the one address and never prefills it", async () => {
    server.use(lookup({ ...info, email_fixed: true }));
    renderApp(`/uitnodiging#${TOKEN}`);
    const email = await screen.findByRole("textbox", { name: "E-mailadres" });
    expect(email).toHaveValue("");
    expect(email).not.toHaveAttribute("readonly");
    expect(
      screen.getByText("Deze uitnodiging is voor één e-mailadres. Vul dat adres in."),
    ).toBeInTheDocument();
  });

  it("makes a new account and offers the app link on a phone", async () => {
    coarsePointer(true);
    let body: unknown = null;
    server.use(
      lookup(),
      http.post(`*${API}/invitations/accept`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(accepted);
      }),
      pairing(),
    );
    const u = userEvent.setup();
    renderApp(`/uitnodiging#${TOKEN}`);
    await fillNewAccount(u);
    await u.click(screen.getByRole("button", { name: "Account maken en meedoen" }));

    const open = await screen.findByRole("link", { name: "Open de Brievie-app" });
    expect(open).toHaveAttribute("href", SERVER_LINK);
    expect(screen.getByText("ABCD-2345")).toBeInTheDocument();
    expect(screen.getByRole("timer")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /QR-code/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Naar Brievie" })).toHaveAttribute("href", "/");
    expect(body).toEqual({
      token: TOKEN,
      account: "new",
      name: "Els",
      email: "els@example.nl",
      password: "een lang wachtwoord 1",
    });
  });

  it("shows the pairing QR code on a computer", async () => {
    coarsePointer(false);
    server.use(
      lookup(),
      http.post(`*${API}/invitations/accept`, () => HttpResponse.json(accepted)),
      pairing(),
    );
    const u = userEvent.setup();
    renderApp(`/uitnodiging#${TOKEN}`);
    await fillNewAccount(u);
    await u.click(screen.getByRole("button", { name: "Account maken en meedoen" }));
    const qr = await screen.findByRole("img", { name: "QR-code om de app te koppelen" });
    expect(qr.getAttribute("data-value")).toContain("ABCD-2345");
    expect(screen.getByText("ABCD-2345")).toBeInTheDocument();
    expect(screen.getByText("Scan deze code met de Brievie-app")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open de Brievie-app" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Naar Brievie" })).toBeInTheDocument();
  });

  it("an existing account signs in to accept, also asking the password when signed in", async () => {
    let body: { account?: string; email?: string; password?: string } = {};
    server.use(
      lookup(),
      http.get(`*${API}/me`, () => HttpResponse.json(user)),
      http.post(`*${API}/invitations/accept`, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(accepted);
      }),
      pairing(),
    );
    const u = userEvent.setup();
    renderApp(`/uitnodiging#${TOKEN}`);
    await u.click(await screen.findByRole("radio", { name: /Ik heb al een account/ }));
    expect(screen.queryByRole("textbox", { name: "Naam" })).not.toBeInTheDocument();
    await u.type(screen.getByRole("textbox", { name: "E-mailadres" }), "anna@example.nl");
    await u.type(screen.getByLabelText("Wachtwoord"), "mijn wachtwoord");
    await u.click(screen.getByRole("button", { name: "Inloggen en meedoen" }));
    await screen.findByRole("link", { name: "Naar Brievie" });
    expect(body).toMatchObject({ account: "existing", email: "anna@example.nl" });
  });

  it("a wrong password shows the server's sentence and keeps the form", async () => {
    server.use(
      lookup(),
      http.post(`*${API}/invitations/accept`, () =>
        HttpResponse.json(
          errorBody("invalid_credentials", "Het e-mailadres of wachtwoord klopt niet."),
          { status: 401 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp(`/uitnodiging#${TOKEN}`);
    await u.click(await screen.findByRole("radio", { name: /Ik heb al een account/ }));
    await u.type(screen.getByRole("textbox", { name: "E-mailadres" }), "anna@example.nl");
    await u.type(screen.getByLabelText("Wachtwoord"), "fout");
    await u.click(screen.getByRole("button", { name: "Inloggen en meedoen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Het e-mailadres of wachtwoord klopt niet.",
    );
    expect(screen.getByRole("textbox", { name: "E-mailadres" })).toHaveValue("anna@example.nl");
    expect(screen.getByRole("button", { name: "Inloggen en meedoen" })).toBeInTheDocument();
  });

  it("an address that already has an account moves to the other choice", async () => {
    server.use(
      lookup(),
      http.post(`*${API}/invitations/accept`, () =>
        HttpResponse.json(
          errorBody(
            "account_exists",
            "Er is al een account met dit e-mailadres. Kies ‘Ik heb al een account’.",
          ),
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp(`/uitnodiging#${TOKEN}`);
    await fillNewAccount(u);
    await u.click(screen.getByRole("button", { name: "Account maken en meedoen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Er is al een account");
    expect(screen.getByRole("radio", { name: /Ik heb al een account/ })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "E-mailadres" })).toHaveValue("els@example.nl");
  });

  it("keeps going to the app when the pairing code cannot be made", async () => {
    server.use(
      lookup(),
      http.post(`*${API}/invitations/accept`, () => HttpResponse.json(accepted)),
      http.post(`*${API}/devices/pairing-codes`, () =>
        HttpResponse.json(errorBody("rate_limited", "Je vraagt te vaak een code aan."), {
          status: 429,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp(`/uitnodiging#${TOKEN}`);
    await fillNewAccount(u);
    await u.click(screen.getByRole("button", { name: "Account maken en meedoen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Je vraagt te vaak een code aan.");
    expect(screen.getByRole("link", { name: "Naar Brievie" })).toBeInTheDocument();
  });
});
