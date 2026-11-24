import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  belasting,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  menuCounts,
  ownDocument,
  papa,
  renderApp,
  server,
  workspace,
} from "../test-utils";

describe("archive", () => {
  it("lists documents with a link per sender", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/post/"));
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Aanslag")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Datum" })).toBeInTheDocument();
  });

  it("names an unknown sender", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, sender: "" }]),
      ),
    );
    renderApp(`/w/${workspace.id}/archief`);
    expect(await screen.findByRole("link", { name: "Onbekende afzender" })).toBeInTheDocument();
  });

  it("shows the AI's proposed sender, type and date as a proposal, not as fact", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([
          {
            ...documentSummary,
            status: "needs_review",
            sender: "",
            document_type: "",
            letter_date: null,
            proposed_sender: "Gemeente Utrecht",
            proposed_document_type: "invoice",
            proposed_letter_date: "2026-11-03",
          },
        ]),
      ),
    );
    renderApp(`/w/${workspace.id}/archief`);
    const link = await screen.findByRole("link", { name: "Gemeente Utrecht (voorstel)" });
    expect(within(link).getByText("Gemeente Utrecht").tagName).toBe("EM");
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Factuur").tagName).toBe("EM");
    expect(within(row).getByText("3 nov 2026").tagName).toBe("EM");
    expect(screen.queryByText("Onbekende afzender")).not.toBeInTheDocument();
  });

  it("is named Archief and says how it differs from Inbox", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    expect(await screen.findByRole("heading", { level: 1, name: "Archief" })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Archief - Brievie"));
    expect(
      screen.getByText(
        "Elke gecontroleerde brief en elk eigen document, om terug te vinden. Wat nog op jou wacht, staat in de Inbox.",
      ),
    ).toBeInTheDocument();
  });

  it("names the workspace in the sidebar and links Archief, not Alle post", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    const nav = await screen.findByRole("navigation");
    // One workspace: its name on top of the menu, not a button (nothing to choose).
    expect(within(nav).getAllByText("Thuis")[0]).toBeInTheDocument();
    expect(within(nav).queryByRole("button", { name: /Werkruimte/ })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Archief" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/archief`,
    );
    expect(within(nav).queryByRole("link", { name: "Alle post" })).not.toBeInTheDocument();
    // One workspace: nothing to choose, so no "Werkruimtes".
    expect(within(nav).queryByRole("link", { name: "Werkruimtes" })).not.toBeInTheDocument();
  });

  it("shows the addressees in a column", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, addressee_ids: [papa.id] }]),
      ),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief`);
    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(screen.getByRole("columnheader", { name: "Op naam van" })).toBeInTheDocument();
    await waitFor(() =>
      expect(within(link.closest("tr") as HTMLElement).getByText("Papa")).toBeInTheDocument(),
    );
  });

  it("filters on a person through the URL", async () => {
    const seen: (string | null)[] = [];
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("person_id"));
        return HttpResponse.json([documentSummary]);
      }),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    const filter = await screen.findByRole("combobox", { name: "Van wie" });
    await screen.findByRole("option", { name: "Papa" });
    await user.selectOptions(filter, "Papa");
    await waitFor(() => expect(router.state.location.search).toEqual({ persoon: papa.id }));
    await waitFor(() => expect(seen).toContain(papa.id));
  });

  it("reads the person filter from the URL", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief?persoon=${papa.id}`);
    const filter = await screen.findByRole("combobox", { name: "Van wie" });
    await waitFor(() => expect(filter).toHaveValue(papa.id));
  });

  it("shows the labels of a letter as chips in a column", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, label_ids: [belasting.id] }]),
      ),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief`);
    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(screen.getByRole("columnheader", { name: "Labels" })).toBeInTheDocument();
    const chip = await within(link.closest("tr") as HTMLElement).findByText("Belastingdienst", {
      selector: ".label-chip",
    });
    expect(chip).toHaveClass("label-blue");
  });

  it("filters on a label through the URL", async () => {
    const seen: (string | null)[] = [];
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("label_id"));
        return HttpResponse.json([documentSummary]);
      }),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    const filter = await screen.findByRole("combobox", { name: "Label" });
    await screen.findByRole("option", { name: "Belastingdienst" });
    await user.selectOptions(filter, "Belastingdienst");
    await waitFor(() => expect(router.state.location.search).toEqual({ label: belasting.id }));
    await waitFor(() => expect(seen).toContain(belasting.id));
  });

  it("reads the label filter from the URL", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief?label=${belasting.id}`);
    const filter = await screen.findByRole("combobox", { name: "Label" });
    await waitFor(() => expect(filter).toHaveValue(belasting.id));
  });

  it("keeps the person filter when a label is chosen", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/archief?persoon=${papa.id}`);
    const filter = await screen.findByRole("combobox", { name: "Label" });
    await screen.findByRole("option", { name: "Belastingdienst" });
    await user.selectOptions(filter, "Belastingdienst");
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ persoon: papa.id, label: belasting.id }),
    );
  });

  it("shows the waiting letters as the Inbox count", async () => {
    server.use(menuCounts({ inbox: 3 }), ...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    const todo = await within(nav).findByRole("link", { name: "Inbox, 3 brieven" });
    expect(todo).toHaveAttribute("href", `/w/${workspace.id}/inbox`);
    expect(within(nav).getByRole("link", { name: "Mensen" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/mensen`,
    );
  });

  it("names a single letter to do in the singular", async () => {
    server.use(menuCounts({ inbox: 1 }), ...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Inbox, 1 brief" })).toBeInTheDocument();
  });

  it("shows a filter value that is not in the list as '(verwijderd)'", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const gone = "c9999999-9999-4999-8999-999999999999";
    renderApp(`/w/${workspace.id}/archief?label=${gone}&persoon=${gone}`);
    const label = await screen.findByRole("combobox", { name: "Label" });
    await screen.findByRole("option", { name: "Belastingdienst" });
    await waitFor(() => expect(label).toHaveValue(gone));
    expect(within(label).getByRole("option", { name: "(verwijderd)" })).toBeInTheDocument();
    const person = screen.getByRole("combobox", { name: "Van wie" });
    await waitFor(() => expect(person).toHaveValue(gone));
    expect(within(person).getByRole("option", { name: "(verwijderd)" })).toBeInTheDocument();
  });

  it("says nothing was found with these filters, and not that the archive is empty", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () => HttpResponse.json([])),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief?label=${belasting.id}`);
    expect(await screen.findByText("Niets gevonden met deze filters.")).toBeVisible();
    expect(screen.queryByText("Nog niets in je archief.")).not.toBeInTheDocument();
  });

  it("says the archive is empty when nothing is filtered, and how letters get there", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () => HttpResponse.json([])),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief`);
    expect(await screen.findByText("Nog niets in je archief.")).toBeVisible();
    expect(
      screen.getByText(
        "Brieven komen hier zodra je ze hebt gecontroleerd. Een eigen document toevoegen kan niet in de browser.",
      ),
    ).toBeVisible();
  });
  it("shows the badge only on a letter the AI had a hand in", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([
          { ...documentSummary, confirmed_by_ai: true },
          { ...documentSummary, id: "2", sender: "Gemeente", auto_label_ids: [belasting.id] },
          { ...documentSummary, id: "3", sender: "Bank" },
          {
            ...documentSummary,
            id: "4",
            sender: "Waterschap",
            auto_label_ids: [belasting.id],
            auto_rule_label_ids: [belasting.id],
          },
        ]),
      ),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief`);
    const rows = await screen.findAllByRole("row");
    const text = (name: string) =>
      within(rows.find((row) => within(row).queryByRole("link", { name })) as HTMLElement);
    expect(text("Belastingdienst").getByText("Automatisch door AI")).toBeVisible();
    expect(text("Gemeente").getByText("Automatisch door AI")).toBeVisible();
    expect(text("Bank").queryByText("Automatisch door AI")).not.toBeInTheDocument();
    // Only a rule's label, put on by the autopilot: not "door AI".
    expect(text("Waterschap").getByText("Automatisch door regel")).toBeVisible();
    expect(text("Waterschap").queryByText("Automatisch door AI")).not.toBeInTheDocument();
  });

  it("filters on letters the AI handled and keeps that in the URL", async () => {
    const seen: (string | null)[] = [];
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("automatic"));
        return HttpResponse.json([documentSummary]);
      }),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    const filter = await screen.findByRole("checkbox", { name: "Automatisch door Brievie" });
    expect(filter).not.toBeChecked();
    expect(seen).not.toContain("true");
    await user.click(filter);
    await waitFor(() => expect(router.state.location.search).toEqual({ automatisch: true }));
    await waitFor(() => expect(seen).toContain("true"));
    expect(screen.getByRole("checkbox", { name: "Automatisch door Brievie" })).toBeChecked();
  });

  it("reads the automatic filter from the URL", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief?automatisch=true`);
    expect(await screen.findByRole("checkbox", { name: "Automatisch door Brievie" })).toBeChecked();
  });
});

describe("archive: letter status", () => {
  const counts = { new: 2, open: 3, waiting: 2, handled: 14, overdue: 1 };

  function statusHandlers(asked: URLSearchParams[] = []) {
    return [
      http.get(`*${API}/workspaces/${workspace.id}/documents/status-counts`, () =>
        HttpResponse.json(counts),
      ),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
        asked.push(new URL(request.url).searchParams);
        return HttpResponse.json([
          { ...documentSummary, letter_status: "waiting", waiting_until: "2026-12-03" },
          {
            ...documentSummary,
            id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b5555",
            letter_status: "open",
            overdue: true,
            sender: "Gemeente",
          },
        ]);
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    ];
  }

  it("shows a status badge per row and Te laat in words", async () => {
    server.use(...statusHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    const row = (await screen.findByRole("link", { name: "Gemeente" })).closest(
      "tr",
    ) as HTMLElement;
    expect(within(row).getByText("Openstaand")).toBeInTheDocument();
    expect(within(row).getByText("Te laat")).toBeInTheDocument();
    const other = screen
      .getByRole("link", { name: "Belastingdienst" })
      .closest("tr") as HTMLElement;
    expect(within(other).getByText("Wacht op reactie tot 3 dec")).toBeInTheDocument();
    expect(within(other).queryByText("Klaar")).not.toBeInTheDocument();
  });

  it("filters on a status and keeps the choice in the URL", async () => {
    const asked: URLSearchParams[] = [];
    server.use(...statusHandlers(asked));
    const user = userEvent.setup();
    renderApp(`/w/${workspace.id}/archief`);
    await user.click(await screen.findByRole("radio", { name: "Openstaand (3)" }));
    await waitFor(() => expect(asked.at(-1)?.get("letter_status")).toBe("open"));
    // A letter in the archive is checked: no "Nieuw".
    expect(screen.queryByRole("radio", { name: "Nieuw (2)" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Wacht op reactie (2)" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Afgehandeld (14)" })).toBeInTheDocument();
  });

  it("filters on late letters only", async () => {
    const asked: URLSearchParams[] = [];
    server.use(...statusHandlers(asked));
    const user = userEvent.setup();
    renderApp(`/w/${workspace.id}/archief`);
    await user.click(await screen.findByRole("checkbox", { name: "Alleen te laat (1)" }));
    await waitFor(() => expect(asked.at(-1)?.get("overdue")).toBe("true"));
  });

  it("reads the status from the URL", async () => {
    const asked: URLSearchParams[] = [];
    server.use(...statusHandlers(asked));
    renderApp(`/w/${workspace.id}/archief?status=wacht&telaat=true`);
    await waitFor(() => expect(asked.at(-1)?.get("letter_status")).toBe("waiting"));
    expect(asked.at(-1)?.get("overdue")).toBe("true");
  });

  it("names the person in the heading and counts only their post", async () => {
    const counted: URLSearchParams[] = [];
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents/status-counts`, ({ request }) => {
        counted.push(new URL(request.url).searchParams);
        return HttpResponse.json({ ...counts, new: 0, overdue: 0 });
      }),
      ...statusHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief?persoon=${papa.id}`);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Archief van Papa" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(counted.at(-1)?.get("person_id")).toBe(papa.id));
    expect(await screen.findByRole("radio", { name: "Openstaand (3)" })).toBeInTheDocument();
  });
});

describe("archive: own documents and its filters", () => {
  function archiveHandlers(asked: URLSearchParams[] = []) {
    return [
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents/archive-facets`, () =>
        HttpResponse.json({
          kinds: [
            { value: "letter", count: 1240 },
            { value: "document", count: 12 },
          ],
          types: [
            { value: "assessment", count: 40 },
            { value: "identity", count: 3 },
          ],
          years: [
            { value: "2026", count: 900 },
            { value: "2025", count: 352 },
          ],
          senders: [{ value: "Belastingdienst", count: 40 }],
        }),
      ),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
        asked.push(new URL(request.url).searchParams);
        return HttpResponse.json([documentSummary, ownDocument]);
      }),
      ...documentHandlers(),
    ];
  }

  it("shows documents and reviewed letters, and asks only for the archive", async () => {
    const asked: URLSearchParams[] = [];
    server.use(...archiveHandlers(asked));
    renderApp(`/w/${workspace.id}/archief`);
    const own = await screen.findByRole("link", { name: "Paspoort" });
    const row = own.closest("tr") as HTMLElement;
    expect(within(row).getByText("Eigen document")).toBeInTheDocument();
    expect(within(row).getByText("Identiteitsbewijs")).toBeInTheDocument();
    expect(within(row).getByText("Geldig tot 12 mrt 2031")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Belastingdienst" })).toBeInTheDocument();
    expect(asked.at(-1)?.get("archived")).toBe("true");
    expect(screen.getByRole("columnheader", { name: "Afzender of titel" })).toBeInTheDocument();
    expect(await screen.findByText("1.252 documenten")).toBeInTheDocument();
  });

  it("an expired document is marked in words", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...ownDocument, expires_on: "2020-01-31" }]),
      ),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief`);
    const row = (await screen.findByRole("link", { name: "Paspoort" })).closest(
      "tr",
    ) as HTMLElement;
    expect(within(row).getByText("Verlopen op 31 jan 2020")).toBeInTheDocument();
  });

  it("facet counts come from the server", async () => {
    server.use(...archiveHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    expect(await screen.findByRole("radio", { name: "Documenten (12)" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Brieven (1240)" })).toBeInTheDocument();
    const type = screen.getByRole("combobox", { name: "Type" });
    expect(within(type).getByRole("option", { name: "Identiteitsbewijs (3)" })).toBeInTheDocument();
    const year = screen.getByRole("combobox", { name: "Jaar" });
    expect(within(year).getByRole("option", { name: "2025 (352)" })).toBeInTheDocument();
    const sender = screen.getByRole("combobox", { name: "Afzender" });
    expect(
      within(sender).getByRole("option", { name: "Belastingdienst (40)" }),
    ).toBeInTheDocument();
  });

  it("filters write to the URL and narrow the list", async () => {
    const asked: URLSearchParams[] = [];
    server.use(...archiveHandlers(asked));
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    await user.click(await screen.findByRole("radio", { name: "Documenten (12)" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ soort: "documenten" }));
    await waitFor(() => expect(asked.at(-1)?.get("kind")).toBe("document"));
    await user.selectOptions(screen.getByRole("combobox", { name: "Type" }), "identity");
    await user.selectOptions(screen.getByRole("combobox", { name: "Jaar" }), "2025");
    await user.click(screen.getByRole("checkbox", { name: "Loopt af" }));
    await waitFor(() =>
      expect(router.state.location.search).toEqual({
        soort: "documenten",
        type: "identity",
        jaar: 2025,
        "loopt-af": true,
      }),
    );
    await waitFor(() => expect(asked.at(-1)?.get("expiring")).toBe("true"));
    const last = asked.at(-1);
    expect([last?.get("document_type"), last?.get("year")]).toEqual(["identity", "2025"]);
  });

  it("chooses a sender from the list, never as typed text", async () => {
    const asked: URLSearchParams[] = [];
    server.use(...archiveHandlers(asked));
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    const sender = await screen.findByRole("combobox", { name: "Afzender" });
    await screen.findByRole("option", { name: "Belastingdienst (40)" });
    await user.selectOptions(sender, "Belastingdienst");
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ afzender: "Belastingdienst" }),
    );
    await waitFor(() => expect(asked.at(-1)?.get("sender")).toBe("Belastingdienst"));
  });

  it("/post goes to /archief keeping persoon and label", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const { router } = renderApp(
      `/w/${workspace.id}/post?persoon=${papa.id}&label=${belasting.id}`,
    );
    await waitFor(() => expect(router.state.location.pathname).toBe(`/w/${workspace.id}/archief`));
    expect(router.state.location.search).toEqual({ persoon: papa.id, label: belasting.id });
    expect(
      await screen.findByRole("heading", { level: 1, name: "Archief van Papa" }),
    ).toBeInTheDocument();
  });
});
