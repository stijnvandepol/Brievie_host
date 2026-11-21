import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { workspacesQuery } from "../api/queries";
import { showDismissed } from "../duplicate-notice";
import {
  API,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  personHandlers,
  renderApp,
  secondWorkspace,
  server,
  twoWorkspaces,
  workspace,
  workspaceHandlers,
} from "../test-utils";

// The old switch looked dead (spec S 2.1): a page stayed mounted when only the workspace in
// the address changed. A switch must draw the other workspace afresh and ask the server.

const homeLetter = { ...documentSummary, sender: "Belastingdienst", inbox_state: "unassigned" };
const officeLetter = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b9999",
  sender: "Leverancier BV",
  inbox_state: "unassigned",
};

function setUp() {
  const asked: string[] = [];
  // The first handler that matches answers: the documents of each workspace come first.
  server.use(
    http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
      asked.push(`home ${new URL(request.url).search}`);
      const state = new URL(request.url).searchParams.get("inbox_state");
      return HttpResponse.json(state === "unassigned" ? [homeLetter] : []);
    }),
    http.get(`*${API}/workspaces/${secondWorkspace.id}/documents`, ({ request }) => {
      asked.push(`office ${new URL(request.url).search}`);
      const state = new URL(request.url).searchParams.get("inbox_state");
      return HttpResponse.json(state === "unassigned" ? [officeLetter] : []);
    }),
    twoWorkspaces(),
    ...loggedInHandlers(),
    ...personHandlers(),
    ...workspaceHandlers(secondWorkspace.id),
  );
  return asked;
}

describe("switching workspaces", () => {
  it("re-asks the server for the other workspace after a switch", async () => {
    const asked = setUp();
    const { router } = renderApp(`/w/${workspace.id}/inbox`);
    expect(await screen.findByRole("link", { name: "Belastingdienst" })).toBeInTheDocument();

    await router.navigate({
      to: "/w/$workspaceId/inbox",
      params: { workspaceId: secondWorkspace.id },
    });

    expect(await screen.findByRole("link", { name: "Leverancier BV" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument();
    expect(asked.some((call) => call.startsWith("office"))).toBe(true);
  });

  it("forgets per-page state of the other workspace", async () => {
    setUp();
    showDismissed(workspace.id);
    const { router } = renderApp(`/w/${workspace.id}/inbox`);
    const note = "Goed. We vragen het niet meer voor deze twee brieven.";
    expect(await screen.findByText(note)).toBeInTheDocument();

    await router.navigate({
      to: "/w/$workspaceId/inbox",
      params: { workspaceId: secondWorkspace.id },
    });

    expect(await screen.findByRole("link", { name: "Leverancier BV" })).toBeInTheDocument();
    expect(screen.queryByText(note)).not.toBeInTheDocument();
  });

  it("refreshing the workspace list does not refetch every workspace query", async () => {
    const asked = setUp();
    let lists = 0;
    server.use(
      http.get(`*${API}/workspaces`, () => {
        lists += 1;
        return HttpResponse.json([workspace, secondWorkspace]);
      }),
    );
    const { queryClient } = renderApp(`/w/${workspace.id}/inbox`);
    await screen.findByRole("link", { name: "Belastingdienst" });
    const before = asked.length;
    const listsBefore = lists;

    await queryClient.invalidateQueries({ queryKey: workspacesQuery.queryKey });

    await waitFor(() => expect(lists).toBeGreaterThan(listsBefore));
    expect(asked.length).toBe(before);
  });
});
