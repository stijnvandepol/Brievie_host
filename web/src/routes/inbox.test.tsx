import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  personHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/inbox`;
const base = `*${API}/workspaces/${workspace.id}`;

function counts(value: { new: number; review: number; unassigned: number }) {
  return http.get(`${base}/inbox/counts`, () => HttpResponse.json(value));
}

describe("inbox", () => {
  it("shows the tabs with their counts and opens the first non-empty one", async () => {
    server.use(
      counts({ new: 1, review: 0, unassigned: 2 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, () => HttpResponse.json([documentSummary])),
    );
    renderApp(path);
    const selected = await screen.findByRole("tab", { name: /Zonder eigenaar/, selected: true });
    expect(selected).toHaveTextContent("2");
    expect(screen.getByRole("tab", { name: /Te controleren/ })).toHaveTextContent("0");
    expect(screen.getByRole("tab", { name: /Nieuw/ })).toHaveTextContent("1");
  });

  it("honours the tab in the URL", async () => {
    const seen: string[] = [];
    server.use(
      counts({ new: 1, review: 1, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("inbox_state") ?? "");
        return HttpResponse.json([{ ...documentSummary, status: "processing" }]);
      }),
    );
    renderApp(`${path}?tab=new`);
    expect(await screen.findByRole("tab", { name: /Nieuw/, selected: true })).toBeInTheDocument();
    await screen.findByText("Deze brief wordt nog gelezen.");
    expect(seen).toEqual(["new"]);
  });

  it("changes the tab in the URL", async () => {
    server.use(
      counts({ new: 0, review: 1, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, () => HttpResponse.json([])),
    );
    const user = userEvent.setup();
    const { router } = renderApp(path);
    await user.click(await screen.findByRole("tab", { name: /Zonder eigenaar/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "unassigned" }));
  });

  it("points a letter to review at the app", async () => {
    server.use(
      counts({ new: 0, review: 1, unassigned: 0 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, status: "needs_review" }]),
      ),
    );
    renderApp(path);
    expect(await screen.findByText("Controleer deze brief in de app")).toBeInTheDocument();
  });

  it("puts a letter on a name and removes the row", async () => {
    const puts: unknown[] = [];
    let assigned = false;
    server.use(
      counts({ new: 0, review: 0, unassigned: 1 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, async ({ request }) => {
        puts.push(await request.json());
        assigned = true;
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      http.get(`${base}/documents`, () => HttpResponse.json(assigned ? [] : [documentSummary])),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    await user.click(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ }));
    await user.click(await screen.findByRole("option", { name: "Papa" }));
    await waitFor(() => expect(puts).toEqual([{ person_ids: [papa.id] }]));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument(),
    );
  });
});
