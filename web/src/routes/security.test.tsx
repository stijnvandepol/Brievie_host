import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
} from "../test-utils";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ title }: { title?: string }) => <svg role="img" aria-label={title} />,
}));

type Status = components["schemas"]["SecondFactorOut"];

const off: Status = {
  enabled: false,
  required: false,
  required_by: "none",
  required_by_workspaces: [],
  grace_until: null,
  recovery_codes_left: 0,
};
const on: Status = { ...off, enabled: true, recovery_codes_left: 8 };
const codes = Array.from({ length: 10 }, (_, i) => `QRSTU-VWXY${i + 2}`);

function status(value: Status) {
  return http.get(`*${API}/me/second-factor`, () => HttpResponse.json(value));
}

describe("Beveiliging", () => {
  it("says it is off and links to the devices", async () => {
    server.use(...loggedInHandlers(), status(off));
    renderApp("/account/beveiliging");

    expect(await screen.findByRole("heading", { level: 1, name: "Beveiliging" })).toBeVisible();
    expect(await screen.findByText("Tweestapsverificatie staat uit.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aanzetten" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Uitzetten" })).not.toBeInTheDocument();
    const phones = screen.getByRole("region", { name: "Gekoppelde telefoons" });
    expect(within(phones).getByRole("link", { name: "Apparaten" })).toBeInTheDocument();
  });

  it("asks the password first in an older session, then shows the code to scan", async () => {
    let attempts = 0;
    let reauthenticated: unknown;
    server.use(
      ...loggedInHandlers(),
      status(off),
      http.post(`*${API}/me/second-factor/setup`, () => {
        attempts += 1;
        if (attempts === 1) {
          return HttpResponse.json(
            errorBody("reauthentication_required", "Vul eerst je wachtwoord opnieuw in."),
            { status: 403 },
          );
        }
        return HttpResponse.json({ secret: "ABCD EFGH", otpauth_uri: "link-voor-de-app" });
      }),
      http.post(`*${API}/auth/reauthenticate`, async ({ request }) => {
        reauthenticated = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/account/beveiliging");
    await u.click(await screen.findByRole("button", { name: "Aanzetten" }));
    await u.click(await screen.findByRole("button", { name: "Tweestapsverificatie aanzetten" }));
    await u.type(await screen.findByLabelText("Wachtwoord"), "mijn wachtwoord");
    await u.click(screen.getByRole("button", { name: "Bevestigen" }));

    expect(await screen.findByText("ABCD EFGH")).toBeInTheDocument();
    expect(reauthenticated).toEqual({ password: "mijn wachtwoord", code: "" });
  });

  it("names who asks for it and until when", async () => {
    server.use(
      ...loggedInHandlers(),
      status({
        ...off,
        required: true,
        required_by: "server",
        grace_until: "2026-12-04T10:00:00Z",
      }),
    );
    renderApp("/account/beveiliging");

    expect(
      await screen.findByText("Op deze server is tweestapsverificatie verplicht."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Zet tweestapsverificatie aan vóór 4 december/)).toBeInTheDocument();
  });

  it("warns when two recovery codes or fewer are left", async () => {
    server.use(...loggedInHandlers(), status({ ...on, recovery_codes_left: 2 }));
    renderApp("/account/beveiliging");

    expect(
      await screen.findByText("Je hebt nog 2 herstelcodes. Maak nieuwe aan."),
    ).toBeInTheDocument();
  });

  it("Uitzetten asks the password and a code", async () => {
    let sent: unknown;
    server.use(
      ...loggedInHandlers(),
      status(on),
      http.delete(`*${API}/me/second-factor`, async ({ request }) => {
        sent = await request.json();
        server.use(status(off));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/account/beveiliging");
    await u.click(await screen.findByRole("button", { name: "Uitzetten" }));
    const form = screen.getByRole("form", { name: "Uitzetten" });
    await u.type(within(form).getByLabelText("Wachtwoord"), "mijn wachtwoord");
    await u.type(within(form).getByRole("textbox", { name: "Code uit de app" }), "123456");
    await u.click(within(form).getByRole("button", { name: "Uitzetten" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Tweestapsverificatie staat uit.");
    expect(sent).toEqual({ password: "mijn wachtwoord", code: "123456" });
  });

  it("offers no Uitzetten while it is required", async () => {
    server.use(...loggedInHandlers(), status({ ...on, required: true, required_by: "server" }));
    renderApp("/account/beveiliging");

    expect(
      await screen.findByText("Uitzetten kan niet: tweestapsverificatie is verplicht."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Uitzetten" })).not.toBeInTheDocument();
  });

  it("makes new recovery codes with a code and shows them once", async () => {
    server.use(
      ...loggedInHandlers(),
      status(on),
      http.post(`*${API}/me/second-factor/recovery-codes`, () =>
        HttpResponse.json({ recovery_codes: codes }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/account/beveiliging");
    await u.click(await screen.findByRole("button", { name: "Nieuwe herstelcodes" }));
    await u.type(screen.getByRole("textbox", { name: "Code uit de app" }), "123456");
    await u.click(screen.getByRole("button", { name: "Nieuwe herstelcodes maken" }));

    const dialog = await screen.findByRole("dialog", { name: "Je herstelcodes" });
    expect(within(dialog).getByText(codes[0] ?? "")).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Ik heb ze bewaard" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Je nieuwe herstelcodes zijn klaar.",
    );
  });
});
