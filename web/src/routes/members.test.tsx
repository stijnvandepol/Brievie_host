import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  asRole,
  errorBody,
  exportState,
  HttpResponse,
  http,
  loggedInHandlers,
  memberHandlers,
  members,
  renderApp,
  server,
  user,
  watchAlerts,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/leden`;
const base = `*${API}/workspaces/${workspace.id}`;
const bram = members[1] as (typeof members)[number];
const anna = members[0] as (typeof members)[number];
/** Anna (the caller) and Bram are both admins. */
const twoAdmins = [anna, { ...bram, role: "admin" }];

describe("members", () => {
  it("says how a member differs from a person", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    expect(
      await screen.findByText(
        "Wie met een eigen account inlogt in Thuis. Op wiens naam brieven staan, regel je bij Personen.",
      ),
    ).toBeInTheDocument();
  });

  it("lists members with their role in a business workspace", async () => {
    server.use(asRole("admin", "business"), ...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    expect(await screen.findByRole("row", { name: /Anna.*Beheerder/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Bram.*Lid/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Leden" })).toBeInTheDocument();
  });

  it("shows no role in a home workspace, but the role is in Rol wijzigen", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    const u = userEvent.setup();
    renderApp(path);
    await screen.findByRole("row", { name: /Bram/ });
    expect(screen.queryByRole("columnheader", { name: "Rol" })).not.toBeInTheDocument();
    expect(screen.queryByText("Beheerder")).not.toBeInTheDocument();
    expect(screen.queryByText("Lid")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Rol wijzigen: Bram" }));
    const dialog = await screen.findByRole("dialog", { name: /Rol van Bram/ });
    expect(within(dialog).getByRole("radio", { name: "Lid" })).toBeChecked();
  });

  it("an admin changes a role with three plain choices", async () => {
    let body: unknown = null;
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.put(`${base}/members/${bram.user_id}/role`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...bram, role: "viewer" });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Rol wijzigen: Bram" }));
    const dialog = await screen.findByRole("dialog", { name: /Rol van Bram/ });
    const radios = within(dialog).getAllByRole("radio");
    expect(radios.map((radio) => radio.getAttribute("aria-label"))).toEqual([
      "Beheerder",
      "Lid",
      "Alleen lezen",
    ]);
    expect(within(dialog).getByRole("radio", { name: "Lid" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "Beheerder" })).toHaveAccessibleDescription(
      "Kan leden uitnodigen en alles instellen.",
    );
    expect(within(dialog).getByRole("radio", { name: "Alleen lezen" })).toHaveAccessibleDescription(
      "Kan post lezen, maar niets wijzigen.",
    );
    await u.click(within(dialog).getByRole("radio", { name: "Alleen lezen" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(body).toEqual({ role: "viewer", confirmed: false, preview_token: "" });
  });

  it("a member sees no admin actions", async () => {
    const asMember = [
      { ...anna, role: "member" },
      { ...bram, role: "admin" },
    ];
    server.use(asRole("member"), ...loggedInHandlers(), ...memberHandlers(asMember));
    renderApp(path);
    await screen.findByRole("row", { name: /Bram/ });
    expect(screen.queryByRole("button", { name: /Rol wijzigen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Verwijderen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Uitnodigen/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Werkruimte verlaten" })).toBeInTheDocument();
  });

  it("a viewer sees no admin actions either", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    await screen.findByRole("row", { name: /Bram/ });
    expect(screen.queryByRole("button", { name: /Rol wijzigen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Verwijderen/ })).not.toBeInTheDocument();
  });

  it("removing asks first and explains what stays", async () => {
    let removed: string | null = null;
    let list = [...members];
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/members`, () => HttpResponse.json(list)),
      http.delete(`${base}/members/${bram.user_id}`, ({ request }) => {
        removed = new URL(request.url).pathname.split("/").at(-1) ?? null;
        list = list.filter((member) => member.user_id !== bram.user_id);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Verwijderen: Bram" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Bram verwijderen?" });
    expect(dialog).toHaveTextContent(
      "Bram kan daarna niet meer bij deze werkruimte. De post blijft hier. Taken van Bram hebben daarna niemand.",
    );
    expect(removed).toBeNull();
    await u.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    await waitFor(() =>
      expect(screen.queryByRole("row", { name: /Bram/ })).not.toBeInTheDocument(),
    );
    expect(removed).toBe(bram.user_id);
  });

  it("cancelling a removal changes nothing", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Verwijderen: Bram" }));
    await u.click(await screen.findByRole("button", { name: "Annuleren" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByRole("row", { name: /Bram/ })).toBeInTheDocument();
  });

  it("the only admin cannot leave or step down, and reads why", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    const own = await screen.findByRole("row", { name: /Anna/ });
    expect(
      within(own).getByText(
        "Je bent de enige beheerder. Maak eerst iemand anders beheerder als je wilt stoppen.",
      ),
    ).toBeInTheDocument();
    expect(within(own).queryByRole("button")).not.toBeInTheDocument();
    const other = screen.getByRole("row", { name: /Bram/ });
    expect(within(other).getByRole("button", { name: "Rol wijzigen: Bram" })).toBeInTheDocument();
    expect(within(other).getByRole("button", { name: "Verwijderen: Bram" })).toBeInTheDocument();
  });

  it("the only member sees nothing to do on their own row", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers([anna]));
    renderApp(path);
    const own = await screen.findByRole("row", { name: /Anna/ });
    expect(within(own).queryByRole("button")).not.toBeInTheDocument();
    expect(within(own).queryByText(/enige beheerder/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uitnodigen" })).toBeInTheDocument();
  });

  it("with a second admin, an admin may leave and change their own role", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers(twoAdmins));
    renderApp(path);
    const own = await screen.findByRole("row", { name: /Anna/ });
    expect(within(own).getByRole("button", { name: "Werkruimte verlaten" })).toBeInTheDocument();
    expect(within(own).getByRole("button", { name: "Rol wijzigen: Anna" })).toBeInTheDocument();
    expect(within(own).queryByText(/enige beheerder/)).not.toBeInTheDocument();
  });

  it("a refused leave shows the server's sentence", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(twoAdmins),
      http.post(`${base}/leave`, () =>
        HttpResponse.json(
          errorBody(
            "last_admin",
            "Een werkruimte heeft altijd minstens één beheerder. Maak eerst iemand anders beheerder.",
          ),
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Werkruimte verlaten?" });
    await u.click(within(dialog).getByRole("button", { name: "Verlaten" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Een werkruimte heeft altijd minstens één beheerder.",
    );
    expect(dialog).toBeInTheDocument();
  });

  it("removing a server admin shows the server's sentence", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.delete(`${base}/members/${bram.user_id}`, () =>
        HttpResponse.json(
          errorBody("server_admin_only", "Alleen een serverbeheerder kan dit lid verwijderen."),
          { status: 403 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Verwijderen: Bram" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Alleen een serverbeheerder kan dit lid verwijderen.",
    );
  });

  it("after leaving the last workspace the app lands on the sign-in page", async () => {
    let signedIn = true;
    server.use(
      http.get(`*${API}/me`, () =>
        signedIn
          ? HttpResponse.json(user)
          : HttpResponse.json(errorBody("not_authenticated", "Log eerst in."), { status: 401 }),
      ),
      http.get(`${base}/members`, () =>
        signedIn
          ? HttpResponse.json(twoAdmins)
          : HttpResponse.json(errorBody("forbidden", "Geen toegang."), { status: 403 }),
      ),
      http.get(`${base}/invitations`, () =>
        signedIn
          ? HttpResponse.json([])
          : HttpResponse.json(errorBody("forbidden", "Geen toegang."), { status: 403 }),
      ),
      http.get(`${base}/my-export`, () =>
        signedIn
          ? HttpResponse.json(exportState())
          : HttpResponse.json(errorBody("forbidden", "Geen toegang."), { status: 403 }),
      ),
      ...loggedInHandlers(),
      http.post(`${base}/leave`, () => {
        signedIn = false;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog");
    const alerts = watchAlerts();
    await u.click(within(dialog).getByRole("button", { name: "Verlaten" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inloggen" })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 100));
    alerts.stop();
    expect(alerts.seen()).toBe(false);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("with other workspaces left, leaving goes to the list of workspaces", async () => {
    let left = false;
    server.use(
      http.get(`*${API}/workspaces`, () =>
        HttpResponse.json(
          left
            ? [
                {
                  id: "5d1e7c3a-2b4f-4a6e-9c8d-1f2a3b4c5555",
                  name: "Bedrijf",
                  kind: "business",
                  role: "member",
                },
                {
                  id: "5d1e7c3a-2b4f-4a6e-9c8d-1f2a3b4c6666",
                  name: "Werk",
                  kind: "business",
                  role: "member",
                },
              ]
            : [workspace],
        ),
      ),
      ...loggedInHandlers(),
      ...memberHandlers(twoAdmins),
      http.post(`${base}/leave`, () => {
        left = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    await u.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Verlaten" }),
    );
    expect(await screen.findByRole("link", { name: "Bedrijf" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("the navigation links Leden", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/personen`);
    server.use(
      ...memberHandlers(),
      http.get(`${base}/persons`, () => HttpResponse.json([])),
    );
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await u.click(within(nav).getByRole("link", { name: "Leden" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Leden" })).toBeInTheDocument();
  });

  it("demoting oneself hides the admin controls at once", async () => {
    let role = "admin";
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role }])),
      http.put(`${base}/members/${anna.user_id}/role`, () => {
        role = "member";
        return HttpResponse.json({ ...anna, role: "member" });
      }),
      ...loggedInHandlers(),
      ...memberHandlers(twoAdmins),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Rol wijzigen: Anna" }));
    const dialog = await screen.findByRole("dialog", { name: /Rol van Anna/ });
    await u.click(within(dialog).getByRole("radio", { name: "Lid" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Rol wijzigen/ })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Uitnodigen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Verwijderen/ })).not.toBeInTheDocument();
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(within(nav).queryByRole("link", { name: "Instellingen" })).not.toBeInTheDocument();
  });

  it("changing somebody else's role keeps the admin controls", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.put(`${base}/members/${bram.user_id}/role`, () =>
        HttpResponse.json({ ...bram, role: "viewer" }),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Rol wijzigen: Bram" }));
    const dialog = await screen.findByRole("dialog", { name: /Rol van Bram/ });
    await u.click(within(dialog).getByRole("radio", { name: "Alleen lezen" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Uitnodigen" })).toBeInTheDocument();
  });

  it("makes the export a secondary action: one primary button on the page", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    expect(await screen.findByRole("button", { name: "Mijn post exporteren" })).toHaveClass(
      "secondary",
    );
  });

  it("a person alone says what to do next", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers([anna]));
    renderApp(path);
    expect(await screen.findByText(/Je bent hier nog alleen/)).toBeInTheDocument();
  });
});

describe("members: managing an account", () => {
  const reset = {
    link: "https://brievie.example.nl/wachtwoord-herstellen#reset-secret-123456",
    expires_at: "2099-11-19T10:00:00Z",
  };

  it("offers account actions only where the server allows them", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    await screen.findByRole("row", { name: /Bram/ });
    expect(screen.getByRole("button", { name: "Account: Bram" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Account: Anna" })).not.toBeInTheDocument();
  });

  it("a member never gets them, even when the flag says so", async () => {
    server.use(asRole("member"), ...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    await screen.findByRole("row", { name: /Bram/ });
    expect(screen.queryByRole("button", { name: /Account:/ })).not.toBeInTheDocument();
  });

  it("opens the devices and sessions of a member", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`*${API}/users/${bram.user_id}/devices`, () => HttpResponse.json([])),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Account: Bram" }));
    await u.click(await screen.findByRole("link", { name: "Apparaten en sessies" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Apparaten en sessies van Bram" }),
    ).toBeInTheDocument();
  });

  it("makes a reset link after asking, shows it once and says whom to give it to", async () => {
    let calls = 0;
    server.use(
      http.post(`*${API}/users/${bram.user_id}/password-reset`, () => {
        calls += 1;
        return HttpResponse.json(reset, { status: 201 });
      }),
      ...loggedInHandlers(),
      ...memberHandlers(),
    );
    const u = userEvent.setup();
    const { queryClient } = renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Account: Bram" }));
    await u.click(await screen.findByRole("button", { name: "Herstellink maken" }));
    const ask = await screen.findByRole("alertdialog", { name: "Herstellink maken voor Bram?" });
    expect(ask).toHaveTextContent("Een eerdere herstellink van Bram werkt daarna niet meer.");
    expect(calls).toBe(0);
    await u.click(within(ask).getByRole("button", { name: "Link maken" }));
    const dialog = await screen.findByRole("dialog", { name: "Herstellink voor Bram" });
    expect(
      within(dialog).getByText("Geef deze link aan Bram. Hij werkt 24 uur en één keer."),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Link" })).toHaveValue(reset.link);
    // A click beside the dialog does not throw the link away; Klaar does.
    await u.click(dialog.closest(".overlay") as HTMLElement);
    expect(screen.getByRole("textbox", { name: "Link" })).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Klaar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const secret = "reset-secret-123456";
    expect(
      JSON.stringify(
        queryClient
          .getMutationCache()
          .getAll()
          .map((m) => m.state.data),
      ),
    ).not.toContain(secret);
    expect(
      JSON.stringify(
        queryClient
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data),
      ),
    ).not.toContain(secret);
    expect(screen.queryByDisplayValue(reset.link)).not.toBeInTheDocument();
    // Opening again asks again; there is no link to be seen.
    await u.click(screen.getByRole("button", { name: "Account: Bram" }));
    await u.click(await screen.findByRole("button", { name: "Herstellink maken" }));
    expect(
      await screen.findByRole("alertdialog", { name: "Herstellink maken voor Bram?" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Link" })).not.toBeInTheDocument();
  });

  it("a reset for a server admin shows the server's sentence", async () => {
    server.use(
      http.post(`*${API}/users/${bram.user_id}/password-reset`, () =>
        HttpResponse.json(
          errorBody("server_admin_only", "Alleen een serverbeheerder mag dit bij dit account."),
          { status: 403 },
        ),
      ),
      ...loggedInHandlers(),
      ...memberHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Account: Bram" }));
    await u.click(await screen.findByRole("button", { name: "Herstellink maken" }));
    const ask = await screen.findByRole("alertdialog");
    await u.click(within(ask).getByRole("button", { name: "Link maken" }));
    expect(await within(ask).findByRole("alert")).toHaveTextContent(
      "Alleen een serverbeheerder mag dit bij dit account.",
    );
  });
});
