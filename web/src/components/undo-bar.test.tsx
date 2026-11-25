import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  API,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  secondWorkspace,
  server,
  twoWorkspaces,
  workspace,
} from "../test-utils";
import { showUndo } from "../undo-trash";

const post = `/w/${workspace.id}/archief`;

describe("undo bar", () => {
  it("has no timer and closes on Sluiten", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    showUndo(workspace.id, documentSummary.id);
    renderApp(post);
    const bar = await screen.findByRole("status");
    expect(bar).toHaveTextContent("De brief staat in de prullenbak.");

    vi.useFakeTimers();
    try {
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
    } finally {
      vi.useRealTimers();
    }
    expect(screen.getByRole("status")).toHaveTextContent("De brief staat in de prullenbak.");

    const user = userEvent.setup();
    await user.click(within(bar).getByRole("button", { name: "Sluiten" }));
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("shows the server's sentence when the undo fails", async () => {
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/restore`, () =>
        HttpResponse.json(
          { code: "document_not_found", message: "Deze brief bestaat niet (meer)." },
          { status: 404 },
        ),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    showUndo(workspace.id, documentSummary.id);
    const user = userEvent.setup();
    renderApp(post);
    const bar = await screen.findByRole("status");
    await user.click(within(bar).getByRole("button", { name: "Ongedaan maken" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Deze brief bestaat niet (meer).");
    expect(screen.getByRole("heading", { level: 1, name: "Archief" })).toBeVisible();
  });

  it("does not follow into another workspace", async () => {
    server.use(
      twoWorkspaces(),
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(`*${API}/workspaces/${secondWorkspace.id}/documents/archive`, () =>
        HttpResponse.json([]),
      ),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/inbox/counts`, () =>
        HttpResponse.json({ new: 0, review: 0, unassigned: 0 }),
      ),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/persons`, () => HttpResponse.json([])),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/labels`, () => HttpResponse.json([])),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/documents/archive-facets`, () =>
        HttpResponse.json({ kinds: [], types: [], years: [], senders: [] }),
      ),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/documents/status-counts`, () =>
        HttpResponse.json({ new: 0, open: 0, waiting: 0, handled: 0, overdue: 0 }),
      ),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/ai`, () =>
        HttpResponse.json({ mode: "off" }),
      ),
    );
    showUndo(workspace.id, documentSummary.id);
    const { router } = renderApp(post);
    await screen.findByRole("status");
    await act(() =>
      router.navigate({
        to: "/w/$workspaceId/archief",
        params: { workspaceId: secondWorkspace.id },
      }),
    );
    await screen.findByText("Nog niets in je archief.");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
