import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  annaPerson,
  asRole,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  memberHandlers,
  members,
  papa,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const qrValues: string[] = [];

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, title }: { value: string; title?: string }) => {
    qrValues.push(value);
    return <svg role="img" aria-label={title} data-testid="qr" />;
  },
}));

const path = `/w/${workspace.id}/leden`;
const base = `*${API}/workspaces/${workspace.id}`;
const LINK = "https://brievie.example.nl/uitnodiging#tok-abcdefghijklmnop";

type Invitation = components["schemas"]["InvitationOut"];

const open: Invitation = {
  id: "e1111111-1111-4111-8111-111111111111",
  role: "member",
  email: "",
  note: "Oma",
  person_id: null,
  person_name: "",
  created_by_name: "Anna",
  created_at: "2026-11-17T10:00:00Z",
  expires_at: "2099-11-24T10:00:00Z",
  status: "open",
};
const expired: Invitation = {
  ...open,
  id: "e2222222-2222-4222-8222-222222222222",
  note: "",
  email: "tante@example.nl",
  status: "expired",
  expires_at: "2026-11-01T10:00:00Z",
};

function created(extra: Partial<components["schemas"]["CreatedInvitationOut"]> = {}) {
  return { invitation: open, link: LINK, home_network_only: false, ...extra };
}

function handlers(list: Invitation[] = [], persons = [papa, annaPerson]) {
  return [
    ...loggedInHandlers(),
    ...memberHandlers(members, list),
    http.get(`${base}/persons`, () => HttpResponse.json(persons)),
  ];
}

describe("inviting", () => {
  it("shows the link once with a QR code", async () => {
    server.use(
      http.post(`${base}/invitations`, () => HttpResponse.json(created(), { status: 201 })),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog", { name: "Iemand uitnodigen" });
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));

    expect(
      await within(dialog).findByRole("img", { name: "QR-code met de uitnodiging" }),
    ).toBeInTheDocument();
    expect(qrValues.at(-1)).toBe(LINK);
    expect(within(dialog).getByRole("textbox", { name: "Link" })).toHaveValue(LINK);
    expect(within(dialog).getByRole("textbox", { name: "Link" })).toHaveAttribute("readonly");
    expect(within(dialog).getByText(/Geldig tot/)).toBeInTheDocument();
    expect(
      within(dialog).queryByText(/Deze link werkt alleen op je thuisnetwerk/),
    ).not.toBeInTheDocument();
  });

  it("copying is a user action and confirms visibly", async () => {
    server.use(
      http.post(`${base}/invitations`, () => HttpResponse.json(created(), { status: 201 })),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    await u.click(await screen.findByRole("button", { name: "Link maken" }));
    const writes = vi.spyOn(navigator.clipboard, "writeText");
    await screen.findByRole("button", { name: "Kopieer link" });
    expect(writes).not.toHaveBeenCalled();
    await u.click(screen.getByRole("button", { name: "Kopieer link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Gekopieerd");
    expect(writes).toHaveBeenCalledWith(LINK);
  });

  it("the default invitation is a member for seven days, with no choices to make", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${base}/invitations`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(created(), { status: 201 });
      }),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog");
    // The advanced choices are folded away.
    expect(within(dialog).queryByRole("radio")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox", { name: /E-mailadres/ })).not.toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    await within(dialog).findByRole("textbox", { name: "Link" });
    expect(body).toEqual({
      role: "member",
      expires_in_days: 7,
      email: "",
      person_id: null,
      note: "",
    });
  });

  it("the more options set role, expiry, address and note", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${base}/invitations`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(created(), { status: 201 });
      }),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog");
    await u.click(within(dialog).getByRole("button", { name: "Meer opties" }));
    expect(within(dialog).getByRole("radio", { name: "Lid" })).toBeChecked();
    await u.click(within(dialog).getByRole("radio", { name: "Meekijker" }));
    await u.selectOptions(within(dialog).getByRole("combobox", { name: "Geldig" }), "30 dagen");
    const email = within(dialog).getByRole("textbox", { name: "E-mailadres" });
    expect(email).toHaveAccessibleDescription("Alleen dit adres kan de uitnodiging gebruiken.");
    await u.type(email, "tante@example.nl");
    await u.type(within(dialog).getByRole("textbox", { name: "Notitie voor jezelf" }), "Tante Els");
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    await within(dialog).findByRole("textbox", { name: "Link" });
    expect(body).toEqual({
      role: "viewer",
      expires_in_days: 30,
      email: "tante@example.nl",
      person_id: null,
      note: "Tante Els",
    });
  });

  it("a person can be chosen as who it is, only persons without an account", async () => {
    let body: { person_id?: string | null } = {};
    server.use(
      http.post(`${base}/invitations`, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(created(), { status: 201 });
      }),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog");
    const who = within(dialog).getByRole("combobox", { name: "Wie is het?" });
    expect(
      within(who)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Iemand nieuw", "Papa"]);
    await u.selectOptions(who, "Papa");
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    await within(dialog).findByRole("textbox", { name: "Link" });
    expect(body.person_id).toBe(papa.id);
  });

  it("asks nothing about who it is when every person has an account", async () => {
    server.use(...handlers([], [annaPerson]));
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("combobox", { name: "Wie is het?" })).not.toBeInTheDocument();
  });

  it("says when the link only works on the home network", async () => {
    server.use(
      http.post(`${base}/invitations`, () =>
        HttpResponse.json(created({ home_network_only: true }), { status: 201 }),
      ),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    await u.click(await screen.findByRole("button", { name: "Link maken" }));
    expect(
      await screen.findByText("Deze link werkt alleen op je thuisnetwerk."),
    ).toBeInTheDocument();
  });

  it("shows the server's sentence when the invitation fails", async () => {
    server.use(
      http.post(`${base}/invitations`, () =>
        HttpResponse.json(errorBody("already_member", "Iemand met dit e-mailadres is al lid."), {
          status: 409,
        }),
      ),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog");
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Iemand met dit e-mailadres is al lid.",
    );
    expect(within(dialog).getByRole("button", { name: "Link maken" })).toBeInTheDocument();
  });

  it("forgets the link when the dialog closes: no cache, no list entry, a fresh form", async () => {
    server.use(
      http.post(`${base}/invitations`, () => HttpResponse.json(created(), { status: 201 })),
      ...handlers([open]),
    );
    const u = userEvent.setup();
    const { queryClient } = renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    await u.click(await screen.findByRole("button", { name: "Link maken" }));
    await screen.findByRole("textbox", { name: "Link" });
    await u.click(screen.getByRole("button", { name: "Klaar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const secret = "tok-abcdefghijklmnop";
    await waitFor(() => {
      const mutations = queryClient.getMutationCache().getAll();
      expect(JSON.stringify(mutations.map((mutation) => mutation.state.data))).not.toContain(
        secret,
      );
    });
    expect(
      JSON.stringify(
        queryClient
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data),
      ),
    ).not.toContain(secret);
    expect(screen.queryByDisplayValue(LINK)).not.toBeInTheDocument();
    // A new dialog starts at the form again.
    await u.click(screen.getByRole("button", { name: "Uitnodigen" }));
    expect(await screen.findByRole("button", { name: "Link maken" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Link" })).not.toBeInTheDocument();
  });

  it("a member does not see Uitnodigen or the open invitations", async () => {
    server.use(asRole("member"), ...handlers([open]));
    renderApp(path);
    await screen.findByRole("row", { name: /Bram/ });
    expect(screen.queryByRole("button", { name: "Uitnodigen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Open uitnodigingen" })).not.toBeInTheDocument();
  });
});

describe("open invitations", () => {
  it("lists them with role and expiry, and marks an expired one", async () => {
    server.use(...handlers([open, expired]));
    renderApp(path);
    const first = await screen.findByRole("row", { name: /Oma/ });
    expect(within(first).getByText("Lid")).toBeInTheDocument();
    expect(within(first).getByText(/Verloopt op/)).toBeInTheDocument();
    const second = screen.getByRole("row", { name: /tante@example.nl/ });
    expect(within(second).getByText("Verlopen")).toBeInTheDocument();
  });

  it("can be renewed with a new link and revoked after asking", async () => {
    let list = [open];
    let revoked = "";
    server.use(
      http.post(`${base}/invitations/${open.id}/renew`, () =>
        HttpResponse.json(created({ link: `${LINK}-nieuw` }), { status: 201 }),
      ),
      http.delete(`${base}/invitations/${open.id}`, ({ request }) => {
        revoked = new URL(request.url).pathname.split("/").at(-1) ?? "";
        list = [];
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      http.get(`${base}/invitations`, () => HttpResponse.json(list)),
      ...memberHandlers(),
      http.get(`${base}/persons`, () => HttpResponse.json([papa])),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Nieuwe link: Oma" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("textbox", { name: "Link" })).toHaveValue(
      `${LINK}-nieuw`,
    );
    await u.click(within(dialog).getByRole("button", { name: "Klaar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await u.click(screen.getByRole("button", { name: "Intrekken: Oma" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Uitnodiging intrekken?" });
    expect(confirm).toHaveTextContent("De link werkt daarna niet meer.");
    expect(revoked).toBe("");
    await u.click(within(confirm).getByRole("button", { name: "Intrekken" }));
    await waitFor(() => expect(screen.queryByRole("row", { name: /Oma/ })).not.toBeInTheDocument());
    expect(revoked).toBe(open.id);
  });
});
