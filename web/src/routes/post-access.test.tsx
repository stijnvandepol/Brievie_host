import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  asRole,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;

const organisation: components["schemas"]["VisibilityOut"] = {
  kind: "business",
  everyone_sees_all: false,
  departments_enabled: true,
  admins_read_all: false,
  sorter_user_ids: [],
  sentences: ["Je post zien alleen jij en wie hem gescand heeft."],
  summary: ["Iedereen ziet zijn eigen post en die van zijn afdelingen."],
};

describe("Zo is Brievie nu ingesteld", () => {
  it("the summary shows the sentences and the links for Organisatie", async () => {
    server.use(
      asRole("admin", "business"),
      http.get(`${base}/visibility`, () => HttpResponse.json(organisation)),
      ...loggedInHandlers(),
    );
    renderApp(`/w/${workspace.id}/ingesteld`);
    expect(
      await screen.findByText("Iedereen ziet zijn eigen post en die van zijn afdelingen."),
    ).toBeInTheDocument();
    const next = screen.getByRole("navigation", { name: "Wat nu?" });
    expect(within(next).getByRole("link", { name: "Afdelingen maken" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/afdelingen`,
    );
    expect(within(next).getByRole("link", { name: "Mensen uitnodigen" })).toBeInTheDocument();
    expect(
      within(next).getByRole("link", { name: "Iemand post laten verdelen" }),
    ).toBeInTheDocument();
    expect(within(next).getByText(/Back-up instellen/)).toBeInTheDocument();
  });

  it("the summary for Thuis offers its own next steps", async () => {
    server.use(...loggedInHandlers());
    renderApp(`/w/${workspace.id}/ingesteld`);
    const next = await screen.findByRole("navigation", { name: "Wat nu?" });
    expect(within(next).getByRole("link", { name: "Iemand uitnodigen" })).toBeInTheDocument();
    expect(within(next).getByRole("link", { name: "Mensen toevoegen" })).toBeInTheDocument();
    expect(within(next).getByRole("link", { name: "Wie ziet wat wijzigen" })).toBeInTheDocument();
  });
});

describe("Wie kan mijn post zien?", () => {
  it("Wie kan mijn post zien is in the account menu for a member", async () => {
    server.use(
      asRole("member", "business"),
      http.get(`${base}/post-access`, () =>
        HttpResponse.json({ sentences: ["Je post zien alleen jij en Finance."] }),
      ),
      ...loggedInHandlers(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/inbox`);
    await u.click(await screen.findByRole("button", { name: "Account" }));
    await u.click(await screen.findByRole("link", { name: "Wie kan mijn post zien?" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Wie kan mijn post zien?" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Je post zien alleen jij en Finance.")).toBeInTheDocument();
  });

  it("no screen says Zakelijk", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () =>
        HttpResponse.json([
          { ...workspace, kind: "business" },
          { ...workspace, id: "5d1e7c3a-2b4f-4a6e-9c8d-1f2a3b4c5555", name: "Thuis 2" },
        ]),
      ),
      ...loggedInHandlers(),
    );
    renderApp("/");
    expect(await screen.findByText("Organisatie")).toBeInTheDocument();
    expect(screen.queryByText(/Zakelijk/i)).toBeNull();
  });
});
