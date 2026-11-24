import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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

const qrValues: string[] = [];

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, title }: { value: string; title?: string }) => {
    qrValues.push(value);
    return <svg role="img" aria-label={title} data-testid="qr" />;
  },
}));

const QR_PAYLOAD =
  '{"v":1,"urls":["https://192.168.1.20:443"],"code":"ABCD-EFGH","ca_sha256":"ab12"}';
const LOCALHOST_PAYLOAD =
  '{"v":1,"urls":["https://localhost:443","https://127.0.0.1"],"code":"ABCD-EFGH","ca_sha256":null}';

function pairingCode(secondsValid: number, code = "ABCD-EFGH", payload = QR_PAYLOAD) {
  return http.post(`*${API}/devices/pairing-codes`, () =>
    HttpResponse.json(
      {
        code,
        expires_at: new Date(Date.now() + secondsValid * 1000).toISOString(),
        qr_payload: payload,
      },
      { status: 201 },
    ),
  );
}

describe("pair the app", () => {
  it("with two-step verification asks the code too, also for a session without it", async () => {
    let asked = 0;
    const sent: unknown[] = [];
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json({ ...user, has_second_factor: true })),
      ...loggedInHandlers(),
      http.post(`*${API}/devices/pairing-codes`, () => {
        asked += 1;
        if (asked === 1)
          return HttpResponse.json(
            errorBody(
              "second_factor_session_required",
              "Log opnieuw in met je code om dit te doen.",
            ),
            { status: 403 },
          );
        return HttpResponse.json(
          {
            code: "ABCD-EFGH",
            expires_at: new Date(Date.now() + 600_000).toISOString(),
            qr_payload: QR_PAYLOAD,
          },
          { status: 201 },
        );
      }),
      http.post(`*${API}/auth/reauthenticate`, async ({ request }) => {
        sent.push(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    await u.type(await screen.findByLabelText(/^wachtwoord/i), "geheim-wachtwoord");
    await u.type(screen.getByRole("textbox", { name: "Code uit de app" }), "123 456");
    await u.click(screen.getByRole("button", { name: "Bevestigen" }));

    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(sent).toEqual([{ password: "geheim-wachtwoord", code: "123456" }]);
  });

  it("asks for the password again when the session is older, then makes the code", async () => {
    let asked = 0;
    const sent: unknown[] = [];
    server.use(
      ...loggedInHandlers(),
      http.post(`*${API}/devices/pairing-codes`, () => {
        asked += 1;
        if (asked === 1)
          return HttpResponse.json(
            errorBody("reauthentication_required", "Vul eerst je wachtwoord opnieuw in."),
            { status: 403 },
          );
        return HttpResponse.json(
          {
            code: "ABCD-EFGH",
            expires_at: new Date(Date.now() + 600_000).toISOString(),
            qr_payload: QR_PAYLOAD,
          },
          { status: 201 },
        );
      }),
      http.post(`*${API}/auth/reauthenticate`, async ({ request }) => {
        sent.push(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));

    const field = await screen.findByLabelText(/^wachtwoord/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await u.type(field, "geheim-wachtwoord");
    await u.click(screen.getByRole("button", { name: "Bevestigen" }));

    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(sent).toEqual([{ password: "geheim-wachtwoord", code: "" }]);
    expect(screen.queryByLabelText(/^wachtwoord/i)).not.toBeInTheDocument();
  });

  it("says so when the password is wrong", async () => {
    server.use(
      ...loggedInHandlers(),
      http.post(`*${API}/devices/pairing-codes`, () =>
        HttpResponse.json(
          errorBody("reauthentication_required", "Vul eerst je wachtwoord opnieuw in."),
          { status: 403 },
        ),
      ),
      http.post(`*${API}/auth/reauthenticate`, () =>
        HttpResponse.json(errorBody("wrong_password", "Je wachtwoord klopt niet."), {
          status: 400,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    await u.type(await screen.findByLabelText(/^wachtwoord/i), "fout");
    await u.click(screen.getByRole("button", { name: "Bevestigen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Je wachtwoord klopt niet.");
  });

  it("renders a QR whose value is the qr_payload, the code, addresses and a countdown", async () => {
    server.use(...loggedInHandlers(), pairingCode(600));
    document.cookie = "csrftoken=tok123; path=/";
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));

    expect(await screen.findByRole("img", { name: /qr-code/i })).toBeInTheDocument();
    expect(qrValues.at(-1)).toBe(QR_PAYLOAD);
    expect(screen.getByRole("heading", { level: 1, name: "Apparaten" })).toBeInTheDocument();
    expect(screen.getByText("ABCD-EFGH")).toBeInTheDocument();
    expect(screen.getByText("https://192.168.1.20:443")).toBeInTheDocument();
    expect(screen.getByText("Eigen CA, vingerafdruk zit in de code")).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent(
      /^\(nog (9 minuten en \d+ seconden|10 minuten)\)$/,
    );
    expect(screen.queryByRole("button", { name: /nieuwe code/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("copies the qr_payload and confirms it in a polite live region", async () => {
    server.use(...loggedInHandlers(), pairingCode(600));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    await u.click(await screen.findByRole("button", { name: "Kopieer koppelgegevens" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Gekopieerd");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(await navigator.clipboard.readText()).toBe(QR_PAYLOAD);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows the payload in a read-only, selectable field when copying is denied", async () => {
    server.use(...loggedInHandlers(), pairingCode(600));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new DOMException("denied"));

    await u.click(await screen.findByRole("button", { name: "Kopieer koppelgegevens" }));

    const field = await screen.findByRole("textbox", { name: /koppelgegevens/i });
    expect(field).toHaveValue(QR_PAYLOAD);
    expect(field).toHaveAttribute("readonly");
    expect(screen.getByRole("status")).not.toHaveTextContent("Gekopieerd");
    expect(screen.getByRole("status")).toHaveTextContent(/kopieer.*zelf/i);
  });

  it("shows the payload in a field when the Clipboard API does not exist", async () => {
    server.use(...loggedInHandlers(), pairingCode(600));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });

    await u.click(await screen.findByRole("button", { name: "Kopieer koppelgegevens" }));

    expect(await screen.findByRole("textbox", { name: /koppelgegevens/i })).toHaveValue(QR_PAYLOAD);
    vi.unstubAllGlobals();
  });

  it("warns when every address in the payload is localhost", async () => {
    server.use(...loggedInHandlers(), pairingCode(600, "ABCD-EFGH", LOCALHOST_PAYLOAD));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Je server staat ingesteld op 'localhost'. Je telefoon kan die niet bereiken. Stel in het bestand .env de naam of het IP-adres van je server in (BRIEVIE_HOSTNAME).",
    );
  });

  it("does not warn when one address is reachable", async () => {
    const mixed =
      '{"v":1,"urls":["https://localhost","https://192.168.1.20"],"code":"ABCD-EFGH","ca_sha256":null}';
    server.use(...loggedInHandlers(), pairingCode(600, "ABCD-EFGH", mixed));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    await screen.findByRole("img", { name: /qr-code/i });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("announces the expiry, moves focus to Nieuwe code and fetches a fresh one", async () => {
    server.use(...loggedInHandlers(), pairingCode(1, "OLD-CODE1"));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    expect(await screen.findByRole("img", { name: /qr-code/i })).toBeInTheDocument();

    const again = await screen.findByRole("button", { name: /nieuwe code/i }, { timeout: 4000 });
    expect(screen.getByRole("status")).toHaveTextContent("Deze code is verlopen.");
    expect(again).toHaveFocus();
    expect(screen.queryByRole("img", { name: /qr-code/i })).not.toBeInTheDocument();
    server.use(pairingCode(600, "NEW-CODE2", LOCALHOST_PAYLOAD));
    await u.click(again);
    expect(await screen.findByRole("img", { name: /qr-code/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("goes to the login screen when the session has ended", async () => {
    server.use(
      ...loggedInHandlers(),
      http.post(`*${API}/devices/pairing-codes`, () => {
        // The session is gone for every call from now on, as on a real server.
        server.use(
          http.get(`*${API}/me`, () =>
            HttpResponse.json(
              { code: "not_authenticated", message: "Je bent niet ingelogd." },
              { status: 401 },
            ),
          ),
        );
        return HttpResponse.json(
          { code: "not_authenticated", message: "Je bent niet ingelogd." },
          { status: 401 },
        );
      }),
    );
    const u = userEvent.setup();
    const { router } = renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeVisible();
  });

  it("shows the server message when no code can be made", async () => {
    server.use(
      ...loggedInHandlers(),
      http.post(`*${API}/devices/pairing-codes`, () =>
        HttpResponse.json({ code: "forbidden", message: "Je mag dit niet doen." }, { status: 403 }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Je mag dit niet doen.");
  });
});
