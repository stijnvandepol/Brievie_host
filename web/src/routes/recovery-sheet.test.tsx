import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, HttpResponse, http, loggedInHandlers, renderApp, server, user } from "../test-utils";

const serverAdmin = { ...user, is_server_admin: true };

function asServerAdmin() {
  server.use(
    http.get(`*${API}/me`, () => HttpResponse.json(serverAdmin)),
    ...loggedInHandlers(),
  );
}

describe("the recovery sheet page", () => {
  const saved: string[] = [];

  beforeEach(() => {
    saved.length = 0;
    URL.createObjectURL = vi.fn(() => "blob:sheet");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("is only in a server admin's menu", async () => {
    asServerAdmin();
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).getByRole("link", { name: "Herstelblad" })).toHaveAttribute(
      "href",
      "/server/herstelblad",
    );
  });

  it("is not in anybody else's menu", async () => {
    server.use(...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).queryByRole("link", { name: "Herstelblad" })).not.toBeInTheDocument();
  });

  it("sends a member who is no server admin home", async () => {
    server.use(...loggedInHandlers());
    renderApp("/server/herstelblad");
    await screen.findByRole("button", { name: "Account" });
    expect(
      screen.queryByRole("heading", { level: 1, name: "Herstelblad" }),
    ).not.toBeInTheDocument();
  });

  it("downloads the sheet as a file", async () => {
    asServerAdmin();
    let posted = 0;
    server.use(
      http.post(`*${API}/server/recovery-sheet`, () => {
        posted += 1;
        return new HttpResponse("<html>blad</html>", {
          headers: { "Content-Type": "text/html; charset=utf-8" },
        });
      }),
    );
    const u = userEvent.setup();
    renderApp("/server/herstelblad");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Herstelblad" }),
    ).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Herstelblad downloaden" }));
    expect(await screen.findByText("Het herstelblad is gedownload.")).toBeInTheDocument();
    expect(posted).toBe(1);
    expect(saved).toEqual(["brievie-herstelblad.html"]);
  });

  it("asks for the password after an old sign-in, then downloads", async () => {
    asServerAdmin();
    let answers = 0;
    let reauthenticated = false;
    server.use(
      http.post(`*${API}/server/recovery-sheet`, () => {
        answers += 1;
        if (!reauthenticated) {
          return HttpResponse.json(
            { code: "reauthentication_required", message: "Vul eerst je wachtwoord opnieuw in." },
            { status: 403 },
          );
        }
        return new HttpResponse("<html>blad</html>", { headers: { "Content-Type": "text/html" } });
      }),
      http.post(`*${API}/auth/reauthenticate`, () => {
        reauthenticated = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/server/herstelblad");
    await u.click(await screen.findByRole("button", { name: "Herstelblad downloaden" }));
    await u.type(await screen.findByLabelText("Wachtwoord"), "mijn-wachtwoord-123");
    await u.click(screen.getByRole("button", { name: "Bevestigen" }));
    expect(await screen.findByText("Het herstelblad is gedownload.")).toBeInTheDocument();
    expect(answers).toBe(2);
    expect(saved).toEqual(["brievie-herstelblad.html"]);
  });

  it("shows the server's sentence when the sheet is made elsewhere", async () => {
    asServerAdmin();
    server.use(
      http.post(`*${API}/server/recovery-sheet`, () =>
        HttpResponse.json(
          {
            code: "recovery_sheet_not_here",
            message: "Op deze server maak je het herstelblad met ./brievie recovery-sheet.",
          },
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp("/server/herstelblad");
    await u.click(await screen.findByRole("button", { name: "Herstelblad downloaden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("./brievie recovery-sheet");
    expect(saved).toEqual([]);
  });
});
