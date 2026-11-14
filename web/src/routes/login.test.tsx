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
} from "../test-utils";

function loggedOut() {
  server.use(
    http.get(`*${API}/setup`, () => HttpResponse.json({ needs_setup: false })),
    http.get(`*${API}/me`, () =>
      HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 }),
    ),
  );
}

describe("login", () => {
  it("shows the server host, one h1 and the lockout hint", async () => {
    loggedOut();
    renderApp("/login");
    expect(await screen.findByRole("heading", { level: 1, name: "Inloggen" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText(`Server: ${window.location.host}`)).toBeInTheDocument();
    expect(screen.getByText(/na vijf mislukte pogingen/i)).toBeInTheDocument();
  });

  it("shows the server's message on 401", async () => {
    loggedOut();
    server.use(
      http.post(`*${API}/auth/login`, () =>
        HttpResponse.json(
          errorBody("invalid_credentials", "Het e-mailadres of wachtwoord klopt niet."),
          { status: 401 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp("/login");
    await u.type(await screen.findByRole("textbox", { name: /e-mailadres/i }), "anna@example.nl");
    await u.type(screen.getByLabelText(/wachtwoord/i), "fout");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Het e-mailadres of wachtwoord klopt niet.",
    );
  });

  it("logs in, sends JSON and lands on the workspace list", async () => {
    loggedOut();
    let body: unknown;
    let contentType: string | null = null;
    server.use(
      http.post(`*${API}/auth/login`, async ({ request }) => {
        body = await request.json();
        contentType = request.headers.get("content-type");
        // From now on the session exists.
        server.use(...loggedInHandlers());
        return HttpResponse.json(user);
      }),
    );
    const u = userEvent.setup();
    const { router } = renderApp("/login");
    await u.type(await screen.findByRole("textbox", { name: /e-mailadres/i }), "anna@example.nl");
    await u.type(screen.getByLabelText(/wachtwoord/i), "een-lang-wachtwoord");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));
    expect(await screen.findByRole("heading", { name: /werkruimtes/i })).toBeInTheDocument();
    expect(body).toEqual({ email: "anna@example.nl", password: "een-lang-wachtwoord" });
    expect(contentType).toContain("application/json");
    expect(router.state.location.pathname).toBe("/");
  });

  it("shows a calm message when the server cannot be reached", async () => {
    loggedOut();
    server.use(http.post(`*${API}/auth/login`, () => HttpResponse.error()));
    const u = userEvent.setup();
    renderApp("/login");
    await u.type(await screen.findByRole("textbox", { name: /e-mailadres/i }), "anna@example.nl");
    await u.type(screen.getByLabelText(/wachtwoord/i), "x");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/niet bereiken/i));
  });

  it("sends a logged-out visitor of a protected page to the login screen", async () => {
    loggedOut();
    const { router } = renderApp("/koppel-app");
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });
});
