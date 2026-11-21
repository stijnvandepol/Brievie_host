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
  papa,
  renderApp,
  server,
  workspace,
} from "../test-utils";

describe("post", () => {
  it("lists documents with a link per sender", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post`);
    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/post/"));
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Aanslag")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Datum brief" })).toBeInTheDocument();
  });

  it("names an unknown sender", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, sender: "" }]),
      ),
    );
    renderApp(`/w/${workspace.id}/post`);
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
    renderApp(`/w/${workspace.id}/post`);
    const link = await screen.findByRole("link", { name: "Gemeente Utrecht (voorstel)" });
    expect(within(link).getByText("Gemeente Utrecht").tagName).toBe("EM");
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Factuur").tagName).toBe("EM");
    expect(within(row).getByText("3 nov 2026").tagName).toBe("EM");
    expect(screen.queryByText("Onbekende afzender")).not.toBeInTheDocument();
  });

  it("is named Alle post and says how it differs from Inbox", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByRole("heading", { level: 1, name: "Alle post" })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Alle post - Brievie"));
    expect(
      screen.getByText(
        "Al je brieven, ook de afgehandelde. Zoek op persoon of label. Wat nog op jou wacht, staat in de Inbox.",
      ),
    ).toBeInTheDocument();
  });

  it("names the workspace in the sidebar and links Alle post", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post`);
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByText("Thuis")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Alle post" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/post`,
    );
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
    renderApp(`/w/${workspace.id}/post`);
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
    const { router } = renderApp(`/w/${workspace.id}/post`);
    const filter = await screen.findByRole("combobox", { name: "Persoon" });
    await screen.findByRole("option", { name: "Papa" });
    await user.selectOptions(filter, "Papa");
    await waitFor(() => expect(router.state.location.search).toEqual({ persoon: papa.id }));
    await waitFor(() => expect(seen).toContain(papa.id));
  });

  it("reads the person filter from the URL", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post?persoon=${papa.id}`);
    const filter = await screen.findByRole("combobox", { name: "Persoon" });
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
    renderApp(`/w/${workspace.id}/post`);
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
    const { router } = renderApp(`/w/${workspace.id}/post`);
    const filter = await screen.findByRole("combobox", { name: "Label" });
    await screen.findByRole("option", { name: "Belastingdienst" });
    await user.selectOptions(filter, "Belastingdienst");
    await waitFor(() => expect(router.state.location.search).toEqual({ label: belasting.id }));
    await waitFor(() => expect(seen).toContain(belasting.id));
  });

  it("reads the label filter from the URL", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post?label=${belasting.id}`);
    const filter = await screen.findByRole("combobox", { name: "Label" });
    await waitFor(() => expect(filter).toHaveValue(belasting.id));
  });

  it("keeps the person filter when a label is chosen", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const user = userEvent.setup();
    const { router } = renderApp(`/w/${workspace.id}/post?persoon=${papa.id}`);
    const filter = await screen.findByRole("combobox", { name: "Label" });
    await screen.findByRole("option", { name: "Belastingdienst" });
    await user.selectOptions(filter, "Belastingdienst");
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ persoon: papa.id, label: belasting.id }),
    );
  });

  it("sums review and unassigned in the sidebar count", async () => {
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/inbox/counts`, () =>
        HttpResponse.json({ new: 5, review: 1, unassigned: 2 }),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    const todo = await within(nav).findByRole("link", { name: "Inbox, 3 brieven" });
    expect(todo).toHaveAttribute("href", `/w/${workspace.id}/inbox`);
    expect(within(nav).getByRole("link", { name: "Mensen" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/mensen`,
    );
  });

  it("names a single letter to do in the singular", async () => {
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/inbox/counts`, () =>
        HttpResponse.json({ new: 0, review: 0, unassigned: 1 }),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Inbox, 1 brief" })).toBeInTheDocument();
  });

  it("shows a filter value that is not in the list as '(verwijderd)'", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const gone = "c9999999-9999-4999-8999-999999999999";
    renderApp(`/w/${workspace.id}/post?label=${gone}&persoon=${gone}`);
    const label = await screen.findByRole("combobox", { name: "Label" });
    await screen.findByRole("option", { name: "Belastingdienst" });
    await waitFor(() => expect(label).toHaveValue(gone));
    expect(within(label).getByRole("option", { name: "(verwijderd)" })).toBeInTheDocument();
    const person = screen.getByRole("combobox", { name: "Persoon" });
    await waitFor(() => expect(person).toHaveValue(gone));
    expect(within(person).getByRole("option", { name: "(verwijderd)" })).toBeInTheDocument();
  });

  it("says there are no letters for these filters, and no post at all without filters", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () => HttpResponse.json([])),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post?label=${belasting.id}`);
    expect(await screen.findByText("Geen brieven met deze filters.")).toBeVisible();
    expect(screen.queryByText("Er is nog geen post.")).not.toBeInTheDocument();
  });

  it("says there is no post yet when nothing is filtered", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () => HttpResponse.json([])),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByText("Er is nog geen post.")).toBeVisible();
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
    renderApp(`/w/${workspace.id}/post`);
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
    const { router } = renderApp(`/w/${workspace.id}/post`);
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
    renderApp(`/w/${workspace.id}/post?automatisch=true`);
    expect(await screen.findByRole("checkbox", { name: "Automatisch door Brievie" })).toBeChecked();
  });
});
