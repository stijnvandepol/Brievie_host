import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { API, HttpResponse, http, loggedInHandlers, renderApp, server } from "../test-utils";

const qrValues: string[] = [];

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, title }: { value: string; title?: string }) => {
    qrValues.push(value);
    return <svg role="img" aria-label={title} data-testid="qr" />;
  },
}));

const QR_PAYLOAD = '{"v":1,"urls":["https://localhost"],"code":"ABCD-EFGH","ca_sha256":null}';

function pairingCode(secondsValid: number, code = "ABCD-EFGH") {
  return http.post(`*${API}/devices/pairing-codes`, () =>
    HttpResponse.json(
      {
        code,
        expires_at: new Date(Date.now() + secondsValid * 1000).toISOString(),
        qr_payload: QR_PAYLOAD,
      },
      { status: 201 },
    ),
  );
}

describe("pair the app", () => {
  it("renders a QR whose value is the qr_payload, the code as text and a countdown", async () => {
    server.use(...loggedInHandlers(), pairingCode(600));
    document.cookie = "csrftoken=tok123; path=/";
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));

    expect(await screen.findByRole("img", { name: /qr-code/i })).toBeInTheDocument();
    expect(qrValues.at(-1)).toBe(QR_PAYLOAD);
    expect(screen.getByText("ABCD-EFGH")).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent(/9:5\d|10:00/);
    expect(screen.queryByRole("button", { name: /nieuwe code/i })).not.toBeInTheDocument();
  });

  it("offers Nieuwe code once the code has expired and fetches a fresh one", async () => {
    server.use(...loggedInHandlers(), pairingCode(1, "OLD-CODE1"));
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: /koppelcode maken/i }));
    expect(await screen.findByText("OLD-CODE1")).toBeInTheDocument();

    const again = await screen.findByRole("button", { name: /nieuwe code/i }, { timeout: 4000 });
    expect(screen.queryByRole("img", { name: /qr-code/i })).not.toBeInTheDocument();
    server.use(pairingCode(600, "NEW-CODE2"));
    await u.click(again);
    expect(await screen.findByText("NEW-CODE2")).toBeInTheDocument();
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
