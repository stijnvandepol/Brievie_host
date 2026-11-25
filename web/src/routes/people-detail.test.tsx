import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  errorBody,
  HttpResponse,
  homeVisibility,
  http,
  loggedInHandlers,
  people,
  peopleHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;
const [, bram, papa, oma, kees] = people as [
  (typeof people)[number],
  (typeof people)[number],
  (typeof people)[number],
  (typeof people)[number],
  (typeof people)[number],
];

function detail(personId: string) {
  return `/w/${workspace.id}/mensen/${personId}`;
}

describe("a person's page", () => {
  it("shows the account facts for an admin", async () => {
    server.use(
      ...loggedInHandlers(),
      ...peopleHandlers(
        people.map((p) =>
          p.id === bram.id
            ? {
                ...p,
                last_sign_in_at: "2026-11-19T09:00:00Z",
                last_app_use_at: "2026-11-20T18:30:00Z",
              }
            : p,
        ),
      ),
    );
    renderApp(detail(bram.id));
    expect(await screen.findByRole("heading", { level: 1, name: "Bram" })).toBeInTheDocument();
    const facts = screen.getByRole("region", { name: "Gegevens van Bram" });
    expect(facts).toHaveTextContent("Actief");
    expect(facts).toHaveTextContent("Lid");
    expect(facts).toHaveTextContent(/Laatst ingelogd op 19 november 2026/);
    expect(facts).toHaveTextContent(/20 november 2026/);
    expect(within(facts).getByRole("button", { name: "Uitschakelen: Bram" })).toBeInTheDocument();
  });

  it("shows when a switched-off account was switched off", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(detail(kees.id));
    const facts = await screen.findByRole("region", { name: "Gegevens van Kees" });
    expect(facts).toHaveTextContent("Uitgeschakeld op");
    expect(facts).toHaveTextContent(/1 november 2026/);
    expect(
      within(facts).getByRole("button", { name: "Opnieuw uitnodigen: Kees" }),
    ).toBeInTheDocument();
  });

  it("shows the invite action for a never signed in person", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(detail(papa.id));
    expect(await screen.findByRole("button", { name: "Uitnodigen: Papa" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Brieven van Papa (2)" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/archief?persoon=${papa.id}`,
    );
  });

  it("shows how long an invitation runs", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(detail(oma.id));
    const facts = await screen.findByRole("region", { name: "Gegevens van Oma" });
    expect(facts).toHaveTextContent("Uitgenodigd tot");
    expect(within(facts).getByRole("button", { name: "Intrekken: Oma" })).toBeInTheDocument();
  });

  it.each([
    [true, "Iedereen in deze werkruimte ziet de post van Papa."],
    [false, "De post van Papa gaat naar wie post verdeelt."],
  ])(
    "says who sees the post of an inactive person (everyone sees all: %s)",
    async (all, sentence) => {
      server.use(
        http.get(`${base}/visibility`, () =>
          HttpResponse.json({ ...homeVisibility, everyone_sees_all: all }),
        ),
        ...loggedInHandlers(),
        ...peopleHandlers(),
      );
      renderApp(detail(papa.id));
      const section = await screen.findByRole("region", { name: "Wie kan de post van Papa zien?" });
      expect(section).toHaveTextContent(sentence);
    },
  );

  it("an active person's page does not explain who else sees the post", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(detail(bram.id));
    await screen.findByRole("heading", { level: 1, name: "Bram" });
    expect(screen.queryByRole("region", { name: /Wie kan de post van Bram zien/ })).toBeNull();
  });

  it("renames a person and archives it", async () => {
    const sent: unknown[] = [];
    server.use(
      http.put(`${base}/persons/${papa.id}`, async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({});
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(detail(papa.id));
    await u.click(await screen.findByRole("button", { name: "Naam en schrijfwijzen" }));
    const dialog = await screen.findByRole("dialog", { name: "Naam en schrijfwijzen" });
    const name = within(dialog).getByRole("textbox", { name: "Naam" });
    await u.clear(name);
    await u.type(name, "Pa");
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(sent[0]).toMatchObject({ display_name: "Pa" }));
    await u.click(screen.getByRole("button", { name: "Archiveren" }));
    await waitFor(() => expect(sent[1]).toMatchObject({ archived: true }));
  });

  it("deleting a person in use shows the server's sentence", async () => {
    server.use(
      http.delete(`${base}/persons/${papa.id}`, () =>
        HttpResponse.json(
          errorBody(
            "person_in_use",
            "Deze persoon is nog in gebruik. Je kunt de persoon wel archiveren.",
          ),
          { status: 409 },
        ),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(detail(papa.id));
    await u.click(await screen.findByRole("button", { name: "Verwijderen: Papa" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Persoon verwijderen?" });
    await u.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("nog in gebruik");
  });

  it("unknown person shows not found", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(detail("a9999999-9999-4999-8999-999999999999"));
    expect(await screen.findByText("We kunnen deze persoon niet vinden.")).toBeInTheDocument();
  });
});

describe("Beheerd door", () => {
  const managed = { ...papa, managed_by_user_id: bram.user_id };

  it("sets a manager after the preview", async () => {
    const sent: unknown[] = [];
    server.use(
      http.put(`${base}/persons/${papa.id}/manager`, async ({ request }) => {
        const body = (await request.json()) as { confirmed: boolean };
        sent.push(body);
        if (!body.confirmed) {
          return HttpResponse.json(
            errorBody(
              "confirmation_required",
              "Bevestig eerst dat deze mensen meer post gaan zien.",
            ),
            { status: 409 },
          );
        }
        return HttpResponse.json({});
      }),
      http.post(`${base}/visibility/preview`, () =>
        HttpResponse.json({
          changes: [{ user_id: bram.user_id, name: "Bram", gains: ["post van Papa"], losses: [] }],
          letter_count: 2,
          needs_confirmation: true,
          sentences: [],
          preview_token: "tok",
        }),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(detail(papa.id));
    await u.selectOptions(
      await screen.findByRole("combobox", { name: "Beheerd door: Papa" }),
      "Bram",
    );
    const preview = await screen.findByRole("alertdialog");
    expect(preview).toHaveTextContent("Bram: post van Papa");
    await u.click(
      within(preview).getByRole("button", { name: "Ja, Bram beheert de post van Papa" }),
    );
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toMatchObject({ user_id: bram.user_id, confirmed: true, preview_token: "tok" });
  });

  it("clears it without a preview", async () => {
    const sent: unknown[] = [];
    server.use(
      http.put(`${base}/persons/${papa.id}/manager`, async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({});
      }),
      http.get(`${base}/visibility`, () =>
        HttpResponse.json({ ...homeVisibility, kind: "business", everyone_sees_all: false }),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers(people.map((p) => (p.id === papa.id ? managed : p))),
    );
    const u = userEvent.setup();
    renderApp(detail(papa.id));
    expect(
      await screen.findByRole("region", { name: "Wie kan de post van Papa zien?" }),
    ).toHaveTextContent("Bram beheert de post van Papa");
    await u.selectOptions(screen.getByRole("combobox", { name: "Beheerd door: Papa" }), "Niemand");
    await waitFor(() =>
      expect(sent).toEqual([
        { user_id: null, private_post_acknowledged: false, confirmed: false, preview_token: "" },
      ]),
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("naming oneself with private post shows the server's sentence", async () => {
    const sentence =
      "Je kunt jezelf geen toegang geven tot privé-post. Gebruik een uitnodiging of noodtoegang.";
    server.use(
      http.put(`${base}/persons/${papa.id}/manager`, () =>
        HttpResponse.json(errorBody("cannot_grant_self", sentence), { status: 409 }),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(detail(papa.id));
    await u.selectOptions(
      await screen.findByRole("combobox", { name: "Beheerd door: Papa" }),
      "Anna",
    );
    expect(await screen.findByText(sentence)).toBeInTheDocument();
  });

  it("switching off offers a manager when there is private post", async () => {
    const calls: string[] = [];
    server.use(
      http.get(`${base}/persons/${bram.id}/private-post`, () =>
        HttpResponse.json({ has_private_post: true }),
      ),
      http.delete(`${base}/members/${bram.user_id}`, () => {
        calls.push("removed");
        return new HttpResponse(null, { status: 204 });
      }),
      http.put(`${base}/persons/${bram.id}/manager`, async ({ request }) => {
        calls.push(`manager ${JSON.stringify(await request.json())}`);
        return HttpResponse.json({});
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(detail(bram.id));
    await u.click(await screen.findByRole("button", { name: "Uitschakelen: Bram" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Account van Bram uitschakelen?",
    });
    await u.selectOptions(
      await within(dialog).findByRole("combobox", { name: "Laat de post beheren door" }),
      "Anna",
    );
    await u.click(within(dialog).getByRole("button", { name: "Uitschakelen" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0]).toBe("removed");
    expect(calls[1]).toContain(people[0]?.user_id ?? "");
  });
});

describe("security review of PR #43", () => {
  it("asks apart before a manager reads private letters", async () => {
    const sent: { private_post_acknowledged?: boolean }[] = [];
    server.use(
      http.put(`${base}/persons/${papa.id}/manager`, async ({ request }) => {
        const body = (await request.json()) as { private_post_acknowledged: boolean };
        sent.push(body);
        if (!body.private_post_acknowledged) {
          return HttpResponse.json(
            errorBody(
              "private_post_acknowledgement_required",
              "Op naam van deze persoon staan privébrieven. Wie de post beheert, leest die ook. Bevestig dat apart.",
            ),
            { status: 409 },
          );
        }
        return HttpResponse.json({});
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(detail(papa.id));
    await u.selectOptions(
      await screen.findByRole("combobox", { name: "Beheerd door: Papa" }),
      "Bram",
    );
    const ask = await screen.findByRole("alertdialog", { name: "Privébrieven laten lezen?" });
    expect(ask).toHaveTextContent(
      "Op naam van Papa staan privébrieven. Als Bram de post beheert, leest die deze privébrieven ook.",
    );
    await u.click(within(ask).getByRole("button", { name: "Ja, Bram leest ze" }));
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]?.private_post_acknowledged).toBe(true);
  });

  it("an open link left on an active person can be withdrawn", async () => {
    server.use(
      ...loggedInHandlers(),
      ...peopleHandlers(
        people.map((p) =>
          p.id === bram.id ? { ...p, invitation_id: "e7777777-7777-4777-8777-777777777777" } : p,
        ),
      ),
    );
    renderApp(detail(bram.id));
    const facts = await screen.findByRole("region", { name: "Gegevens van Bram" });
    expect(facts).toHaveTextContent("Open uitnodiging");
    expect(within(facts).getByRole("button", { name: "Intrekken: Bram" })).toBeInTheDocument();
  });

  it("activity of an account that is in another workspace too is not shown", async () => {
    server.use(
      ...loggedInHandlers(),
      ...peopleHandlers(
        people.map((p) =>
          p.id === bram.id ? { ...p, activity_hidden: true, last_sign_in_at: null } : p,
        ),
      ),
    );
    renderApp(detail(bram.id));
    const facts = await screen.findByRole("region", { name: "Gegevens van Bram" });
    expect(facts).toHaveTextContent("Niet te zien");
  });
});
