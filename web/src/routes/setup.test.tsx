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
  twoWorkspaces,
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
  await u.type(await screen.findByRole("textbox", { name: /naam van de werkruimte/i }), "Ons huis");
  await u.type(screen.getByRole("textbox", { name: /^je naam/i }), "Anna");
  await u.type(screen.getByRole("textbox", { name: /e-mailadres/i }), "anna@example.nl");
  await u.type(screen.getByLabelText(/^wachtwoord/i), "een-lang-wachtwoord");
  await u.type(screen.getByRole("textbox", { name: /installatiecode/i }), "abc-def");
}

describe("setup", () => {
  it("sends workspace_kind home when the Thuis card is chosen", async () => {
    needsSetup();
    let body: unknown;
    let contentType: string | null = null;
    server.use(
      http.post(`*${API}/setup`, async ({ request }) => {
        body = await request.json();
        contentType = request.headers.get("content-type");
        // After setup the server is set up and the session exists.
        server.use(twoWorkspaces(), ...loggedInHandlers());
        return HttpResponse.json({ user, workspace }, { status: 201 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/setup");

    await u.click(await screen.findByRole("radio", { name: /thuis/i }));
    await fillIn(u);
    await u.click(screen.getByRole("button", { name: /^start/i }));

    await waitFor(() => expect(body).toBeDefined());
    expect(body).toEqual({
      workspace_kind: "home",
      workspace_name: "Ons huis",
      name: "Anna",
      email: "anna@example.nl",
      password: "een-lang-wachtwoord",
      setup_token: "abc-def",
      extra_labels: [],
    });
    expect(contentType).toContain("application/json");
    expect(await screen.findByRole("heading", { name: /werkruimtes/i })).toBeInTheDocument();
  });

  it("sends workspace_kind business when the Zakelijk card is chosen", async () => {
    needsSetup();
    let body: { workspace_kind?: string } = {};
    server.use(
      http.post(`*${API}/setup`, async ({ request }) => {
        body = (await request.json()) as typeof body;
        server.use(twoWorkspaces(), ...loggedInHandlers());
        return HttpResponse.json({ user, workspace }, { status: 201 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/setup");
    await u.click(await screen.findByRole("radio", { name: /zakelijk/i }));
    await fillIn(u);
    await u.click(screen.getByRole("button", { name: /^start/i }));
    await waitFor(() => expect(body.workspace_kind).toBe("business"));
    // Setting up lands on the workspace list; wait for it, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: /werkruimtes/i })).toBeVisible();
  });

  it("asks for departments with Zakelijk and sends them as extra labels", async () => {
    needsSetup();
    let body: { extra_labels?: string[] } = {};
    server.use(
      http.post(`*${API}/setup`, async ({ request }) => {
        body = (await request.json()) as typeof body;
        server.use(twoWorkspaces(), ...loggedInHandlers());
        return HttpResponse.json({ user, workspace }, { status: 201 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/setup");
    await screen.findByRole("radio", { name: /zakelijk/i });
    expect(screen.queryByRole("textbox", { name: "Afdelingen" })).not.toBeInTheDocument();
    await u.click(screen.getByRole("radio", { name: /zakelijk/i }));
    expect(
      screen.getByText("Bijvoorbeeld: Sales, Finance, HR. Je krijgt per afdeling een label."),
    ).toBeInTheDocument();
    await u.type(screen.getByRole("textbox", { name: "Afdelingen" }), "Sales, Finance, HR, ");
    await fillIn(u);
    await u.click(screen.getByRole("button", { name: /^start/i }));
    await waitFor(() => expect(body.extra_labels).toEqual(["Sales", "Finance", "HR"]));
    // Setting up lands on the workspace list; wait for it, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: /werkruimtes/i })).toBeVisible();
  });

  it("sends no extra labels when Thuis is chosen after typing departments", async () => {
    needsSetup();
    let body: { extra_labels?: string[] } = {};
    server.use(
      http.post(`*${API}/setup`, async ({ request }) => {
        body = (await request.json()) as typeof body;
        server.use(twoWorkspaces(), ...loggedInHandlers());
        return HttpResponse.json({ user, workspace }, { status: 201 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/setup");
    await u.click(await screen.findByRole("radio", { name: /zakelijk/i }));
    await u.type(screen.getByRole("textbox", { name: "Afdelingen" }), "Sales");
    await u.click(screen.getByRole("radio", { name: /thuis/i }));
    expect(screen.queryByRole("textbox", { name: "Afdelingen" })).not.toBeInTheDocument();
    await fillIn(u);
    await u.click(screen.getByRole("button", { name: /^start/i }));
    await waitFor(() => expect(body.extra_labels).toEqual([]));
    // Setting up lands on the workspace list; wait for it, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: /werkruimtes/i })).toBeVisible();
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
        server.use(twoWorkspaces(), ...loggedInHandlers());
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
    // Setting up lands on the workspace list; wait for it, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: /werkruimtes/i })).toBeVisible();
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
