import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API,
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

/** A phone: every `max-width` query matches, every `min-width` query does not. */
function phoneScreen() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("post on a phone", () => {
  it("shows a card per letter instead of the table", async () => {
    phoneScreen();
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([
          { ...documentSummary, status: "needs_review", addressee_ids: [papa.id] },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);

    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/post/"));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const card = link.closest("li") as HTMLElement;
    expect(within(card).getByText("Aanslag")).toBeInTheDocument();
    expect(within(card).getByText("Controleren")).toBeInTheDocument();
    expect(await within(card).findByText("Papa")).toBeInTheDocument();
  });
});

describe("status in Alle post", () => {
  it("says Naam kiezen for a read letter without a name, as Inbox does", async () => {
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, status: "done", inbox_state: "unassigned" }]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    expect(within(row as HTMLElement).getByText("Naam kiezen")).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText("Klaar")).not.toBeInTheDocument();
  });

  it("says Klaar only when nothing waits", async () => {
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([
          { ...documentSummary, status: "done", inbox_state: "", addressee_ids: [papa.id] },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    expect(within(row as HTMLElement).getByText("Klaar")).toBeInTheDocument();
  });
});
