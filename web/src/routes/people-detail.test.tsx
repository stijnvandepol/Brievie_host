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
      `/w/${workspace.id}/post?persoon=${papa.id}`,
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
