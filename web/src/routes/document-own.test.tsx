import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  documentDetail,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  trashedLetter,
  workspace,
} from "../test-utils";

type DocumentOut = components["schemas"]["DocumentOut"];
type DetailsIn = components["schemas"]["DocumentDetailsIn"];

const path = `/w/${workspace.id}/post/${documentSummary.id}`;
const detailsUrl = `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/details`;

/** The page's letter as an own document: a passport with an end date, no tasks. */
const passport: DocumentOut = {
  ...documentDetail,
  kind: "document",
  title: "Paspoort",
  sender: "",
  document_type: "identity",
  letter_date: "2025-06-01",
  expires_on: "2031-03-12",
  remind_days: 30,
  retain_until: null,
  letter_status: "",
  summary: "",
  summary_by_ai: false,
  tasks: [],
  confirmed_by_ai: false,
};

function asViewer() {
  return http.get(`*${API}/workspaces`, () =>
    HttpResponse.json([{ ...workspace, role: "viewer" }]),
  );
}

describe("the page of an own document", () => {
  it("shows its details and no review, task or letter blocks", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers(passport));
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Paspoort" })).toBeVisible();
    const details = screen.getByRole("region", { name: "Gegevens" });
    expect(within(details).getByText("Identiteitsbewijs")).toBeVisible();
    expect(within(details).getByText("12 mrt 2031, herinnering 30 dagen vooraf")).toBeVisible();
    expect(within(details).getByText("Van wie")).toBeVisible();
    expect(within(details).getByText("Eigen document")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Zelf controleren" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Brief afhandelen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Markeer als/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Afzender")).not.toBeInTheDocument();
  });

  it("editing the title calls updateDocumentDetails with only the title", async () => {
    const sent: DetailsIn[] = [];
    server.use(
      http.patch(detailsUrl, async ({ request }) => {
        const body = (await request.json()) as DetailsIn;
        sent.push(body);
        return HttpResponse.json({ ...passport, title: body.title || passport.title });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(passport),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Gegevens aanpassen" }));
    const title = screen.getByRole("textbox", { name: "Titel" });
    await user.clear(title);
    await user.type(title, "Paspoort nieuw");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      title: "Paspoort nieuw",
      document_type: "",
      expires_on: null,
      clear_expires_on: false,
      remind_days: 0,
      retain_until: null,
      clear_retain_until: false,
    });
  });

  it("clearing the end date sends clear_expires_on", async () => {
    const sent: DetailsIn[] = [];
    server.use(
      http.patch(detailsUrl, async ({ request }) => {
        sent.push((await request.json()) as DetailsIn);
        return HttpResponse.json({ ...passport, expires_on: null });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(passport),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Gegevens aanpassen" }));
    await user.clear(screen.getByLabelText("Geldig tot"));
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]?.clear_expires_on).toBe(true);
    expect(sent[0]?.expires_on).toBeNull();
    expect(sent[0]?.title).toBe("");
  });

  it("trash asks the retention question first", async () => {
    const trashed: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/trash`, () => {
        trashed.push(documentSummary.id);
        return HttpResponse.json(trashedLetter({ warning: "Bewaren tot 1 januari 2099." }));
      }),
      ...loggedInHandlers(),
      ...documentHandlers({ ...passport, retain_until: "2099-01-01" }),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Verwijderen" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Bewaren tot 1 jan 2099. Weet je het zeker?",
    });
    expect(
      within(dialog).getByText(
        "Je kunt het naar de prullenbak verplaatsen. Voorgoed wissen kan pas als de datum voorbij is of iemand de datum weghaalt.",
      ),
    ).toBeVisible();
    expect(trashed).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Naar de prullenbak" }));
    await waitFor(() => expect(trashed).toHaveLength(1));
  });

  it("a viewer sees the details and no edit controls", async () => {
    server.use(asViewer(), ...loggedInHandlers(), ...documentHandlers(passport));
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Paspoort" })).toBeVisible();
    expect(screen.getByText("Identiteitsbewijs")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Gegevens aanpassen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verwijderen" })).not.toBeInTheDocument();
  });

  it("the failure banner offers to read the text again", async () => {
    const retried: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/retry`, () => {
        retried.push(documentSummary.id);
        return HttpResponse.json({ ...passport, status: "processing", failure_code: "" });
      }),
      ...loggedInHandlers(),
      ...documentHandlers({ ...passport, failure_code: "internal_error" }),
    );
    const user = userEvent.setup();
    renderApp(path);
    expect(
      await screen.findByText(/De tekst is niet gelezen. Het document staat wel in het archief./),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Opnieuw proberen" }));
    await waitFor(() => expect(retried).toHaveLength(1));
    expect(await screen.findByText("Dit document wordt nog gelezen.")).toBeVisible();
  });
});

describe("Bewaren tot on a letter", () => {
  it("an editor sets it; only retain_until is sent", async () => {
    const sent: DetailsIn[] = [];
    server.use(
      http.patch(detailsUrl, async ({ request }) => {
        const body = (await request.json()) as DetailsIn;
        sent.push(body);
        return HttpResponse.json({ ...documentDetail, retain_until: body.retain_until });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Bewaren tot aanpassen" }));
    await user.type(screen.getByLabelText("Bewaren tot"), "2033-01-01");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ retain_until: "2033-01-01", title: "", document_type: "" });
  });
});
