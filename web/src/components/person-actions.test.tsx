import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  API,
  aiHandlers,
  asRole,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  people,
  peopleForMember,
  peopleHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ title }: { title?: string }) => <svg role="img" aria-label={title} />,
}));

const path = `/w/${workspace.id}/mensen`;
const base = `*${API}/workspaces/${workspace.id}`;
const LINK = "https://brievie.example.nl/uitnodiging#tok-abcdefghijklmnop";
const [, bram, papa, oma, kees] = people as [
  (typeof people)[number],
  (typeof people)[number],
  (typeof people)[number],
  (typeof people)[number],
  (typeof people)[number],
];

function created() {
  return {
    invitation: {
      id: "e9999999-9999-4999-8999-999999999999",
      role: "member",
      email: "",
      note: "",
      person_id: papa.id,
      person_name: "Papa",
      created_by_name: "Anna",
      created_at: "2026-11-21T10:00:00Z",
      expires_at: "2099-11-28T10:00:00Z",
      status: "open",
    },
    link: LINK,
    home_network_only: false,
  };
}

function privatePost(personId: string, has: boolean) {
  return http.get(`${base}/persons/${personId}/private-post`, () =>
    HttpResponse.json({ has_private_post: has }),
  );
}

describe("Mensen: what an admin does on a row", () => {
  it("invites a never signed in person from the row", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      privatePost(papa.id, false),
      http.post(`${base}/invitations`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(created(), { status: 201 });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen: Papa" }));
    const dialog = await screen.findByRole("dialog", { name: "Papa uitnodigen" });
    expect(within(dialog).queryByRole("combobox", { name: "Wie is het?" })).toBeNull();
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    expect(await within(dialog).findByRole("textbox", { name: "Link" })).toHaveValue(LINK);
    expect(body).toMatchObject({ person_id: papa.id, email: "" });
  });

  it("reinvite prefills the former address", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      privatePost(kees.id, false),
      http.post(`${base}/invitations`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(created(), { status: 201 });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Opnieuw uitnodigen: Kees" }));
    const dialog = await screen.findByRole("dialog", { name: "Kees uitnodigen" });
    expect(within(dialog).getByRole("textbox", { name: "E-mailadres" })).toHaveValue(
      "kees@example.nl",
    );
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    await within(dialog).findByRole("textbox", { name: "Link" });
    expect(body).toMatchObject({ person_id: kees.id, email: "kees@example.nl" });
  });

  it("email required shows the server sentence at the address field", async () => {
    const sentence =
      "Vul het e-mailadres van deze persoon in. Alleen dat adres kan de uitnodiging gebruiken.";
    server.use(
      privatePost(papa.id, true),
      http.post(`${base}/invitations`, () =>
        HttpResponse.json(errorBody("email_required", sentence), { status: 422 }),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen: Papa" }));
    const dialog = await screen.findByRole("dialog", { name: "Papa uitnodigen" });
    expect(
      await within(dialog).findByText(
        "Op naam van Papa staan privébrieven. Alleen dit adres kan de uitnodiging gebruiken.",
      ),
    ).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    const field = within(dialog).getByRole("textbox", { name: "E-mailadres" });
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(within(dialog).getByText(sentence)).toBeInTheDocument();
  });

  it("resend shows the new link once and says the old one stopped", async () => {
    let renewed = "";
    server.use(
      http.post(`${base}/invitations/:id/renew`, ({ params }) => {
        renewed = String(params.id);
        return HttpResponse.json(created(), { status: 201 });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Opnieuw versturen: Oma" }));
    const dialog = await screen.findByRole("dialog", { name: "Uitnodiging" });
    expect(within(dialog).getByText("De oude link werkt niet meer.")).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Link" })).toHaveValue(LINK);
    expect(renewed).toBe(oma.invitation_id);
  });

  it("withdraw asks first and refreshes the row to Niet actief", async () => {
    let list = [...people];
    let revoked = "";
    server.use(
      http.get(`${base}/people`, () => HttpResponse.json(list)),
      http.delete(`${base}/invitations/:id`, ({ params }) => {
        revoked = String(params.id);
        list = list.map((p) =>
          p.id === oma.id ? { ...p, state: "inactive", invitation_id: null } : p,
        );
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Intrekken: Oma" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Uitnodiging van Oma intrekken?",
    });
    expect(revoked).toBe("");
    await u.click(within(dialog).getByRole("button", { name: "Intrekken" }));
    await waitFor(() =>
      expect(screen.getByRole("row", { name: /^Oma/ })).toHaveTextContent("Niet actief"),
    );
    expect(revoked).toBe(oma.invitation_id);
  });

  it("marks an expired invitation and still offers sending it again", async () => {
    server.use(
      ...loggedInHandlers(),
      ...peopleHandlers(
        people.map((p) => (p.id === oma.id ? { ...p, invitation_expired: true } : p)),
      ),
    );
    renderApp(path);
    const row = await screen.findByRole("row", { name: /^Oma/ });
    expect(row).toHaveTextContent("Verlopen");
    expect(within(row).getByRole("button", { name: "Opnieuw versturen: Oma" })).toBeEnabled();
  });

  it("switch off warns about private post", async () => {
    let removed = "";
    server.use(
      privatePost(bram.id, true),
      http.delete(`${base}/members/:userId`, ({ params }) => {
        removed = String(params.userId);
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Uitschakelen: Bram" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Account van Bram uitschakelen?",
    });
    expect(
      await within(dialog).findByText(
        "Op naam van Bram staan privébrieven. Na uitschakelen kan niemand ze lezen, tot je Bram opnieuw uitnodigt of iemand de post laat beheren.",
      ),
    ).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Uitschakelen" }));
    await waitFor(() => expect(removed).toBe(bram.user_id));
  });

  it("switch off without private post has no warning", async () => {
    let asked = false;
    server.use(
      http.get(`${base}/persons/${bram.id}/private-post`, () => {
        asked = true;
        return HttpResponse.json({ has_private_post: false });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await screen.findByRole("row", { name: /^Bram/ });
    // Asked only when the dialog opens: the list never shows who has private post.
    expect(asked).toBe(false);
    await u.click(screen.getByRole("button", { name: "Uitschakelen: Bram" }));
    const dialog = await screen.findByRole("alertdialog");
    await waitFor(() => expect(asked).toBe(true));
    expect(within(dialog).queryByText(/privébrieven/)).toBeNull();
  });

  it("a member sees no actions", async () => {
    server.use(
      asRole("member"),
      ...aiHandlers(),
      ...loggedInHandlers(),
      ...peopleHandlers(peopleForMember),
    );
    renderApp(path);
    await screen.findByRole("row", { name: /^Papa/ });
    for (const name of [
      /Uitnodigen:/,
      /Opnieuw versturen/,
      /Intrekken/,
      /Uitschakelen/,
      /Rol wijzigen/,
    ]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });
});
