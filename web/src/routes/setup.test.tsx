import { screen, waitFor } from "@testing-library/react";
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
  workspace,
} from "../test-utils";

function needsSetup() {
  server.use(
    http.get(`*${API}/setup`, () => HttpResponse.json({ needs_setup: true })),
    http.get(`*${API}/me`, () =>
      HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 }),
    ),
  );
}

async function fillIn(u: ReturnType<typeof userEvent.setup>) {
  await u.type(await screen.findByRole("textbox", { name: /^Naam, bijvoorbeeld/ }), "Ons huis");
  await u.type(screen.getByRole("textbox", { name: /^je naam/i }), "Anna");
  await u.type(screen.getByRole("textbox", { name: /e-mailadres/i }), "anna@example.nl");
  await u.type(screen.getByLabelText(/^wachtwoord/i), "een-lang-wachtwoord");
  await u.type(screen.getByRole("textbox", { name: /installatiecode/i }), "abc-def");
}

describe("setup", () => {
  it("setup shows Thuis and Organisatie with their sentences and no Afdelingen field", async () => {
    needsSetup();
    const u = userEvent.setup();
    renderApp("/setup");
    expect(await screen.findByRole("radio", { name: /Thuis/ })).toBeInTheDocument();
    expect(
      screen.getByText("Voor jezelf en je gezin. Iedereen ziet alle post, behalve wat privé is."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Voor een bedrijf, praktijk of vereniging. Ieder ziet zijn eigen post en die van zijn afdeling.",
      ),
    ).toBeInTheDocument();
    await u.click(screen.getByRole("radio", { name: /Organisatie/ }));
    expect(screen.queryByRole("textbox", { name: "Afdelingen" })).toBeNull();
    expect(
      screen.getByRole("textbox", {
        name: "Naam, bijvoorbeeld 'Familie de Vries' of 'Bakkerij Jansen'",
      }),
    ).toBeInTheDocument();
  });

  it("setup sends no extra_labels and opens Zo is Brievie nu ingesteld", async () => {
    needsSetup();
    let body: unknown;
    let contentType: string | null = null;
    server.use(
      http.post(`*${API}/setup`, async ({ request }) => {
        body = await request.json();
        contentType = request.headers.get("content-type");
        // After setup the server is set up and the session exists.
        server.use(...loggedInHandlers());
        return HttpResponse.json({ user, workspace }, { status: 201 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/setup");
    await u.click(await screen.findByRole("radio", { name: /Organisatie/ }));
    await fillIn(u);
    await u.click(screen.getByRole("button", { name: /^start/i }));
    await waitFor(() => expect(body).toBeDefined());
    expect(body).toEqual({
      workspace_kind: "business",
      workspace_name: "Ons huis",
      name: "Anna",
      email: "anna@example.nl",
      password: "een-lang-wachtwoord",
      setup_token: "abc-def",
      extra_labels: [],
    });
    expect(contentType).toContain("application/json");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Zo is Brievie nu ingesteld" }),
    ).toBeInTheDocument();
  });

  it("shows the server message for a wrong installation code and stays on the form", async () => {
    needsSetup();
    server.use(
      http.post(`*${API}/setup`, () =>
        HttpResponse.json(errorBody("setup_token_invalid", "Deze installatiecode klopt niet."), {
          status: 403,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/setup");
    await u.click(await screen.findByRole("radio", { name: /thuis/i }));
    await fillIn(u);
    await u.click(screen.getByRole("button", { name: /^start/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Deze installatiecode klopt niet.");
  });

  it("keeps the installation code field free of mobile keyboard help", async () => {
    needsSetup();
    renderApp("/setup");
    const field = await screen.findByRole("textbox", { name: /installatiecode/i });
    expect(field).toHaveAttribute("autocapitalize", "none");
    expect(field).toHaveAttribute("autocorrect", "off");
    expect(field).toHaveAttribute("spellcheck", "false");
    expect(field).toHaveAttribute("autocomplete", "off");
  });

  it("sends the installation code without surrounding spaces", async () => {
    needsSetup();
    let body: { setup_token?: string } = {};
    server.use(
      http.post(`*${API}/setup`, async ({ request }) => {
        body = (await request.json()) as typeof body;
        server.use(...loggedInHandlers());
        return HttpResponse.json({ user, workspace }, { status: 201 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/setup");
    await u.click(await screen.findByRole("radio", { name: /thuis/i }));
    await fillIn(u);
    await u.type(screen.getByRole("textbox", { name: /installatiecode/i }), "  ");
    await u.click(screen.getByRole("button", { name: /^start/i }));
    await waitFor(() => expect(body.setup_token).toBe("abc-def"));
    // Setting up lands on the summary; wait for it, so no navigation outlives the test.
    expect(
      await screen.findByRole("heading", { level: 1, name: "Zo is Brievie nu ingesteld" }),
    ).toBeVisible();
  });

  it("explains where to find the installation code", async () => {
    needsSetup();
    renderApp("/setup");
    expect(
      await screen.findByText(
        "Je vindt de installatiecode in het logboek van de Brievie-container.",
      ),
    ).toBeInTheDocument();
  });

  it("goes to the login screen when the server is already set up", async () => {
    server.use(
      http.get(`*${API}/setup`, () => HttpResponse.json({ needs_setup: false })),
      http.get(`*${API}/me`, () =>
        HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), {
          status: 401,
        }),
      ),
    );
    const { router } = renderApp("/setup");
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });
});
