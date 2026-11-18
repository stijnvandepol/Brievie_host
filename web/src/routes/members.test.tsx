import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  asRole,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  memberHandlers,
  members,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/leden`;
const base = `*${API}/workspaces/${workspace.id}`;
const bram = members[1] as (typeof members)[number];
const anna = members[0] as (typeof members)[number];

describe("members", () => {
  it("lists members with their role", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    expect(await screen.findByRole("row", { name: /Anna.*Beheerder/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Bram.*Lid/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Leden" })).toBeInTheDocument();
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
      "Meekijker",
    ]);
    expect(within(dialog).getByRole("radio", { name: "Lid" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "Beheerder" })).toHaveAccessibleDescription(
      "Kan leden uitnodigen en alles instellen.",
    );
    expect(within(dialog).getByRole("radio", { name: "Meekijker" })).toHaveAccessibleDescription(
      "Kan alles lezen, maar niets wijzigen.",
    );
    await u.click(within(dialog).getByRole("radio", { name: "Meekijker" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(body).toEqual({ role: "viewer" });
  });

  it("a member sees no admin actions", async () => {
    server.use(asRole("member"), ...loggedInHandlers(), ...memberHandlers());
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

  it("the last admin sees why they cannot leave", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.post(`${base}/leave`, () =>
        HttpResponse.json(
          errorBody(
            "last_admin",
            "Je bent de enige beheerder. Maak eerst iemand anders beheerder.",
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
      "Je bent de enige beheerder. Maak eerst iemand anders beheerder.",
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
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.post(`${base}/leave`, () => {
        signedIn = false;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Verlaten" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inloggen" })).toBeInTheDocument();
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
      ...memberHandlers(),
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

  it("a person alone says what to do next", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers([anna]));
    renderApp(path);
    expect(await screen.findByText(/Je bent hier nog alleen/)).toBeInTheDocument();
  });
});
