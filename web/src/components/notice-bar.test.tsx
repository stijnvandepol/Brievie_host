import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  documentHandlers,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  secondWorkspace,
  server,
  twoWorkspaces,
  workspace,
} from "../test-utils";

const notice = {
  id: "f1111111-1111-4111-8111-111111111111",
  code: "password_reset_by_admin",
  message: "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne.",
  created_at: "2026-05-03T10:00:00Z",
  can_dismiss: true,
};

describe("notices", () => {
  it("shows until it is marked seen", async () => {
    let list = [notice];
    let body: unknown = null;
    server.use(
      http.get(`*${API}/me/notices`, () => HttpResponse.json(list)),
      http.post(`*${API}/me/notices/seen`, async ({ request }) => {
        body = await request.json();
        list = [];
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    expect(
      await screen.findByText("Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."),
    ).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Gezien" }));
    await waitFor(() =>
      expect(screen.queryByText(/opnieuw ingesteld door Anne/)).not.toBeInTheDocument(),
    );
    expect(body).toEqual({ notice_ids: [notice.id] });
  });

  it("a notice that cannot be dismissed has no Gezien button", async () => {
    server.use(
      http.get(`*${API}/me/notices`, () => HttpResponse.json([{ ...notice, can_dismiss: false }])),
      ...loggedInHandlers(),
    );
    renderApp("/koppel-app");
    expect(await screen.findByText(/opnieuw ingesteld door Anne/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Gezien" })).not.toBeInTheDocument();
  });

  it("shows one bar per notice, on every page of the app", async () => {
    server.use(
      http.get(`*${API}/me/notices`, () =>
        HttpResponse.json([
          notice,
          {
            ...notice,
            id: "f2222222-2222-4222-8222-222222222222",
            message: "Een apparaat is ingetrokken.",
          },
        ]),
      ),
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/members`, () => HttpResponse.json([])),
      http.get(`*${API}/workspaces/${workspace.id}/invitations`, () => HttpResponse.json([])),
    );
    renderApp(`/w/${workspace.id}/leden`);
    expect(await screen.findByText("Een apparaat is ingetrokken.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Gezien" })).toHaveLength(2);
  });

  it("stays quiet when the notices cannot be read", async () => {
    server.use(
      http.get(`*${API}/me/notices`, () => HttpResponse.error()),
      ...loggedInHandlers(),
    );
    renderApp("/koppel-app");
    await screen.findByRole("heading", { level: 1, name: "Apparaten" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("notices per workspace (decision B7)", () => {
  const aboutHome = {
    ...notice,
    id: "f2222222-2222-4222-8222-222222222222",
    message: "Noodtoegang staat aan in Thuis.",
    workspace_id: workspace.id,
  };
  const aboutOffice = {
    ...notice,
    id: "f3333333-3333-4333-8333-333333333333",
    message: "Noodtoegang staat aan in Bedrijf.",
    workspace_id: secondWorkspace.id,
  };
  const aboutMe = { ...notice, workspace_id: null };

  function setUp() {
    server.use(
      http.get(`*${API}/me/notices`, () => HttpResponse.json([aboutHome, aboutOffice, aboutMe])),
      twoWorkspaces(),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
  }

  it("shows a workspace notice only in its workspace", async () => {
    setUp();
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByText("Noodtoegang staat aan in Thuis.")).toBeVisible();
    expect(screen.queryByText("Noodtoegang staat aan in Bedrijf.")).not.toBeInTheDocument();
  });

  it("shows account notices everywhere", async () => {
    setUp();
    renderApp("/koppel-app");
    expect(await screen.findByText(/opnieuw ingesteld door Anne/)).toBeVisible();
    expect(screen.queryByText(/Noodtoegang staat aan/)).not.toBeInTheDocument();
    cleanup();
    setUp();
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByText(/opnieuw ingesteld door Anne/)).toBeVisible();
  });

  it("shows all on /werkruimtes", async () => {
    setUp();
    renderApp("/werkruimtes");
    expect(await screen.findByText("Noodtoegang staat aan in Thuis.")).toBeVisible();
    expect(screen.getByText("Noodtoegang staat aan in Bedrijf.")).toBeVisible();
    expect(screen.getByText(/opnieuw ingesteld door Anne/)).toBeVisible();
  });
});
