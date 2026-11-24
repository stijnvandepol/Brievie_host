import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
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
} from "../test-utils";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, title }: { value: string; title?: string }) => (
    <svg role="img" aria-label={title} data-value={value} />
  ),
}));

const secret = {
  secret: "ABCD EFGH ABCD EFGH",
  otpauth_uri: "link-voor-de-app",
};
const codes = Array.from({ length: 10 }, (_, i) => `ABCDE-FGH${String(i).padStart(2, "J")}`);

function loggedOut() {
  server.use(
    http.get(`*${API}/setup`, () => HttpResponse.json({ needs_setup: false })),
    http.get(`*${API}/me`, () =>
      HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 }),
    ),
  );
}

function signedInFromNowOn() {
  server.use(twoWorkspaces(), ...loggedInHandlers(), ...documentHandlers());
}

async function password(u: ReturnType<typeof userEvent.setup>, step: string) {
  server.use(
    http.post(`*${API}/auth/login`, () =>
      HttpResponse.json({ step, expires_in_seconds: 300 }, { status: 202 }),
    ),
  );
  renderApp("/login");
  await u.type(await screen.findByRole("textbox", { name: /e-mailadres/i }), "anna@example.nl");
  await u.type(screen.getByLabelText(/wachtwoord/i), "een-lang-wachtwoord");
  await u.click(screen.getByRole("button", { name: /^inloggen$/i }));
}

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("the code step after the password", () => {
  it("asks the code and lands on Mijn post", async () => {
    loggedOut();
    let sent: unknown;
    server.use(
      http.post(`*${API}/auth/login/code`, async ({ request }) => {
        sent = await request.json();
        signedInFromNowOn();
        return HttpResponse.json({ ...user, has_second_factor: true });
      }),
    );
    const u = userEvent.setup();
    await password(u, "code");

    expect(await screen.findByRole("heading", { level: 1, name: "Code invullen" })).toBeVisible();
    expect(screen.getByText("Typ de code uit je authenticator-app.")).toBeInTheDocument();
    const field = screen.getByRole("textbox", { name: "Code" });
    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");
    await u.type(field, "123 456");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));

    expect(await screen.findByRole("heading", { name: "Mijn post" })).toBeInTheDocument();
    expect(sent).toEqual({ code: "123456", recovery_code: "" });
  });

  it("shows the server's sentence for a wrong code", async () => {
    loggedOut();
    server.use(
      http.post(`*${API}/auth/login/code`, () =>
        HttpResponse.json(
          errorBody("second_factor_invalid", "Code klopt niet. Probeer het opnieuw."),
          { status: 401 },
        ),
      ),
    );
    const u = userEvent.setup();
    await password(u, "code");
    await u.type(await screen.findByRole("textbox", { name: "Code" }), "000000");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Code klopt niet. Probeer het opnieuw.",
    );
  });

  it("switches to a recovery code for a lost phone", async () => {
    loggedOut();
    let sent: unknown;
    server.use(
      http.post(`*${API}/auth/login/code`, async ({ request }) => {
        sent = await request.json();
        signedInFromNowOn();
        return HttpResponse.json(user);
      }),
    );
    const u = userEvent.setup();
    await password(u, "code");
    await u.click(
      await screen.findByRole("button", { name: "Telefoon kwijt? Gebruik een herstelcode." }),
    );
    await u.type(screen.getByRole("textbox", { name: "Herstelcode" }), "abcde-fghjk");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));

    expect(await screen.findByRole("heading", { name: "Mijn post" })).toBeInTheDocument();
    expect(sent).toEqual({ code: "", recovery_code: "abcde-fghjk" });
  });

  it("goes back to the password when the step expired", async () => {
    loggedOut();
    server.use(
      http.post(`*${API}/auth/login/code`, () =>
        HttpResponse.json(
          errorBody("sign_in_expired", "Je bent te lang bezig geweest. Log opnieuw in."),
          { status: 401 },
        ),
      ),
    );
    const u = userEvent.setup();
    await password(u, "code");
    await u.type(await screen.findByRole("textbox", { name: "Code" }), "123456");
    await u.click(screen.getByRole("button", { name: /^inloggen$/i }));
    await u.click(await screen.findByRole("button", { name: "Opnieuw inloggen" }));

    expect(await screen.findByRole("textbox", { name: /e-mailadres/i })).toBeInTheDocument();
  });
});

describe("setting up two-step verification at sign-in", () => {
  it("sets it up, shows the codes once and signs in", async () => {
    loggedOut();
    let confirmed: unknown;
    server.use(
      http.post(`*${API}/me/second-factor/setup`, () => HttpResponse.json(secret)),
      http.post(`*${API}/me/second-factor/setup/confirm`, async ({ request }) => {
        confirmed = await request.json();
        signedInFromNowOn();
        return HttpResponse.json({ recovery_codes: codes });
      }),
    );
    const u = userEvent.setup();
    await password(u, "setup");

    expect(
      await screen.findByRole("heading", { level: 1, name: "Tweestapsverificatie aanzetten" }),
    ).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Tweestapsverificatie aanzetten" }));
    const qr = await screen.findByRole("img", { name: "QR-code voor je authenticator-app" });
    expect(qr).toHaveAttribute("data-value", secret.otpauth_uri);
    expect(screen.getByText(secret.secret)).toBeInTheDocument();
    expect(screen.getByText(/Microsoft Authenticator/)).toBeInTheDocument();
    await u.type(screen.getByRole("textbox", { name: "Code uit de app" }), "654321");
    await u.click(screen.getByRole("button", { name: "Aanzetten" }));

    const dialog = await screen.findByRole("dialog", { name: "Je herstelcodes" });
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(10);
    expect(confirmed).toEqual({ code: "654321" });

    // The dialog does not close before the codes are saved.
    await u.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Je herstelcodes" })).toBeInTheDocument();

    await u.click(within(dialog).getByRole("button", { name: "Ik heb ze bewaard" }));
    expect(await screen.findByRole("heading", { name: "Mijn post" })).toBeInTheDocument();
  });

  it("downloads the codes as a text file", async () => {
    loggedOut();
    server.use(
      http.post(`*${API}/me/second-factor/setup`, () => HttpResponse.json(secret)),
      http.post(`*${API}/me/second-factor/setup/confirm`, () =>
        HttpResponse.json({ recovery_codes: codes }),
      ),
    );
    let saved: Blob | undefined;
    const create = vi.fn((blob: Blob) => {
      saved = blob;
      return "blob:herstelcodes";
    });
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const u = userEvent.setup();
    await password(u, "setup");
    await u.click(await screen.findByRole("button", { name: "Tweestapsverificatie aanzetten" }));
    await u.type(await screen.findByRole("textbox", { name: "Code uit de app" }), "654321");
    await u.click(screen.getByRole("button", { name: "Aanzetten" }));
    await u.click(await screen.findByRole("button", { name: "Downloaden" }));

    expect(click).toHaveBeenCalled();
    // Not revoked at once: Safari may still be reading the file.
    expect(revoke).not.toHaveBeenCalled();
    expect(saved).toBeDefined();
    const text = await saved?.text();
    for (const code of codes) expect(text).toContain(code);
  });

  it("shows the clock sentence when a code matches only far away", async () => {
    loggedOut();
    const drift =
      "De klok van je telefoon of van de server loopt niet gelijk. Zet op je telefoon de tijd op automatisch.";
    server.use(
      http.post(`*${API}/me/second-factor/setup`, () => HttpResponse.json(secret)),
      http.post(`*${API}/me/second-factor/setup/confirm`, () =>
        HttpResponse.json(errorBody("second_factor_clock_drift", drift), { status: 409 }),
      ),
    );
    const u = userEvent.setup();
    await password(u, "setup");
    await u.click(await screen.findByRole("button", { name: "Tweestapsverificatie aanzetten" }));
    await u.type(await screen.findByRole("textbox", { name: "Code uit de app" }), "654321");
    await u.click(screen.getByRole("button", { name: "Aanzetten" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(drift);
  });
});

describe("the other ways in reach the code step", () => {
  it("a reset link", async () => {
    loggedOut();
    server.use(
      http.post(`*${API}/auth/password-reset`, () =>
        HttpResponse.json({ step: "code", expires_in_seconds: 300 }, { status: 202 }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/wachtwoord-herstellen#reset-token-xyz");
    await u.type(await screen.findByLabelText("Nieuw wachtwoord"), "een-nieuw-lang-wachtwoord");
    await u.type(screen.getByLabelText("Herhaal nieuw wachtwoord"), "een-nieuw-lang-wachtwoord");
    await u.click(screen.getByRole("button", { name: "Wachtwoord opslaan" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Code invullen" })).toBeVisible();
  });
});
