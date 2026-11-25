import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  asRole,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  trashedLetter,
  trashHandlers,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/prullenbak`;

/** A moment `days` calendar days from today, at noon local time. */
function inDays(days: number): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + days, 12).toISOString();
}

const older = trashedLetter({
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b5555",
  sender: "Gemeente",
  trashed_at: "2026-11-08T10:00:00Z",
  trashed_by_name: "",
  purge_after: inDays(1),
});

describe("trash", () => {
  it("lists rows with when, by whom and the days left", async () => {
    server.use(
      ...loggedInHandlers(),
      ...trashHandlers([
        trashedLetter({ purge_after: inDays(12) }),
        older,
        trashedLetter({
          id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b6666",
          sender: "",
          proposed_sender: "",
          purge_after: new Date(Date.now() - 60_000).toISOString(),
        }),
      ]),
    );
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Prullenbak" })).toBeVisible();
    expect(
      screen.getByText(
        "Brieven in de prullenbak worden na 30 dagen voorgoed gewist. Tot die tijd kun je ze terugzetten.",
      ),
    ).toBeVisible();
    await screen.findByText("Nog 12 dagen");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    const [first, second, third] = rows;
    if (!first || !second || !third) throw new Error("three rows expected");
    expect(within(first).getByText("Belastingdienst")).toBeVisible();
    expect(within(first).getByText("Verwijderd op 9 november 2026 door Anna")).toBeVisible();
    expect(within(first).getByText("Nog 12 dagen")).toBeVisible();
    expect(within(second).getByText("Verwijderd op 8 november 2026")).toBeVisible();
    expect(within(second).getByText("Nog 1 dag")).toBeVisible();
    expect(within(third).getByText("Onbekende afzender")).toBeVisible();
    expect(within(third).getByText("Wordt vandaag gewist")).toBeVisible();
  });

  it("is in the navigation, last before Instellingen", async () => {
    server.use(...loggedInHandlers(), ...trashHandlers());
    renderApp(path);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    const names = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(names.indexOf("Instellingen")).toBe(names.indexOf("Prullenbak") + 1);
    expect(names).toContain("Inbox");
  });

  it("shows De prullenbak is leeg. and no Prullenbak legen when empty", async () => {
    server.use(...loggedInHandlers(), ...trashHandlers());
    renderApp(path);
    expect(await screen.findByText("De prullenbak is leeg.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Prullenbak legen" })).not.toBeInTheDocument();
  });

  it("lets a member restore and shows no admin buttons", async () => {
    const items = [trashedLetter()];
    const restored: string[] = [];
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      ...trashHandlers(items),
      http.post(`*${API}/workspaces/${workspace.id}/documents/:id/restore`, ({ params }) => {
        restored.push(String(params.id));
        items.splice(0);
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Terugzetten: Belastingdienst" }));
    await waitFor(() => expect(restored).toEqual([trashedLetter().id]));
    expect(await screen.findByText("De brief is teruggezet.")).toBeVisible();
    expect(await screen.findByText("De prullenbak is leeg.")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /Definitief verwijderen/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prullenbak legen" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Brievie houdt nog geen bewaartermijn bij/)).not.toBeInTheDocument();
  });

  it("offers a viewer no Terugzetten", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...trashHandlers([trashedLetter()]));
    renderApp(path);
    expect(await screen.findByText("Belastingdienst")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Terugzetten/ })).not.toBeInTheDocument();
  });

  it("shows an admin the retention sentence", async () => {
    server.use(...loggedInHandlers(), ...trashHandlers([trashedLetter()]));
    renderApp(path);
    expect(
      await screen.findByText(
        "Brievie houdt nog geen bewaartermijn bij. Moet je een brief bewaren, bijvoorbeeld een factuur voor de belasting? Let daar dan zelf op voordat je hem wist.",
      ),
    ).toBeVisible();
  });

  it("lets an admin purge one letter after a danger question", async () => {
    const items = [trashedLetter(), older];
    const purged: string[] = [];
    server.use(
      ...loggedInHandlers(),
      ...trashHandlers(items),
      http.delete(`*${API}/workspaces/${workspace.id}/trash/:id`, ({ params }) => {
        purged.push(String(params.id));
        items.splice(0, 1);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(
      await screen.findByRole("button", { name: "Definitief verwijderen: Belastingdienst" }),
    );
    const dialog = await screen.findByRole("alertdialog", { name: "Brief voorgoed wissen?" });
    expect(
      within(dialog).getByText(
        "De brief en de gescande pagina's worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een back-up die eerder is gemaakt, heeft de brief nog. Daar blijft hij in tot die back-up verloopt.",
      ),
    ).toBeVisible();
    expect(purged).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Definitief verwijderen" }));
    await waitFor(() => expect(purged).toEqual([trashedLetter().id]));
    await waitFor(() => expect(screen.queryByText("Belastingdienst")).not.toBeInTheDocument());
    expect(screen.getByText("Gemeente")).toBeVisible();
  });

  it("lets an admin empty the trash up to the newest row shown", async () => {
    const items = [trashedLetter({ trashed_at: "2026-11-09T10:00:00Z" }), older];
    const bodies: unknown[] = [];
    server.use(
      ...loggedInHandlers(),
      ...trashHandlers(items),
      http.post(`*${API}/workspaces/${workspace.id}/trash/empty`, async ({ request }) => {
        bodies.push(await request.json());
        items.splice(0);
        return HttpResponse.json({ document_count: 2 });
      }),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Prullenbak legen" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Prullenbak legen?" });
    expect(
      within(dialog).getByText(
        "2 brieven in de prullenbak worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een back-up die eerder is gemaakt, heeft deze brieven nog. Daar blijven ze in tot die back-up verloopt.",
      ),
    ).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Prullenbak legen" }));
    await waitFor(() => expect(bodies).toEqual([{ up_to: "2026-11-09T10:00:00Z" }]));
    expect(await screen.findByText("De prullenbak is leeg.")).toBeVisible();
  });

  it("counts every letter the empty takes, also beyond the listed ones", async () => {
    server.use(...loggedInHandlers(), ...trashHandlers([trashedLetter(), older], 600));
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Prullenbak legen" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Prullenbak legen?" });
    expect(
      within(dialog).getByText(
        "600 brieven in de prullenbak worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een back-up die eerder is gemaakt, heeft deze brieven nog. Daar blijven ze in tot die back-up verloopt.",
      ),
    ).toBeVisible();
  });

  it("keeps the question open with the server's sentence when a purge is refused", async () => {
    server.use(
      ...loggedInHandlers(),
      ...trashHandlers([trashedLetter()]),
      http.delete(`*${API}/workspaces/${workspace.id}/trash/:id`, () =>
        HttpResponse.json(
          { code: "document_retained", message: "Deze brief moet nog bewaard worden." },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(
      await screen.findByRole("button", { name: "Definitief verwijderen: Belastingdienst" }),
    );
    const dialog = await screen.findByRole("alertdialog", { name: "Brief voorgoed wissen?" });
    await user.click(within(dialog).getByRole("button", { name: "Definitief verwijderen" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Deze brief moet nog bewaard worden.",
    );
  });
});

describe("Bewaren tot in the trash", () => {
  const kept = trashedLetter({ retain_until: "2099-01-01", can_clear_retention: true });

  it("says a retained letter cannot be purged and lets an admin take the date away", async () => {
    const sent: Record<string, unknown>[] = [];
    server.use(
      http.patch(
        `*${API}/workspaces/${workspace.id}/documents/${kept.id}/details`,
        async ({ request }) => {
          sent.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json({});
        },
      ),
      ...loggedInHandlers(),
      ...trashHandlers([kept]),
    );
    const user = userEvent.setup();
    renderApp(path);
    expect(
      await screen.findByText("Bewaren tot 1 jan 2099: niet definitief te wissen."),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^Bewaren tot weghalen/ }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]?.clear_retain_until).toBe(true);
    expect(sent[0]?.title).toBe("");
  });

  it("the person the document belongs to takes the date away too", async () => {
    server.use(asRole("member"), ...loggedInHandlers(), ...trashHandlers([kept]));
    renderApp(path);
    expect(await screen.findByRole("button", { name: /^Bewaren tot weghalen/ })).toBeVisible();
  });

  it("another member sees the date but cannot take it away", async () => {
    const other = { ...kept, can_clear_retention: false };
    server.use(asRole("member"), ...loggedInHandlers(), ...trashHandlers([other]));
    renderApp(path);
    expect(
      await screen.findByText("Bewaren tot 1 jan 2099: niet definitief te wissen."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: /Bewaren tot weghalen/ })).not.toBeInTheDocument();
  });
});
