import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  belasting,
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
    const selected = await screen.findByRole("tab", { name: /Op niemands naam/, selected: true });
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
    await user.click(await screen.findByRole("tab", { name: /Op niemands naam/ }));
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
    expect(await screen.findByText("Controleer deze brief in de app.")).toBeInTheDocument();
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

describe("inbox labels and roles", () => {
  it("shows the labels of a letter as chips", async () => {
    server.use(
      counts({ new: 0, review: 0, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, label_ids: [belasting.id] }]),
      ),
    );
    renderApp(path);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    expect(
      await within(row as HTMLElement).findByText("Belastingdienst", { selector: ".label-chip" }),
    ).toHaveClass("label-blue");
  });

  it("disables the person picker for a viewer", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      counts({ new: 0, review: 0, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, () => HttpResponse.json([documentSummary])),
    );
    renderApp(path);
    expect(await screen.findByRole("button", { name: /Op naam van/ })).toBeDisabled();
  });
});

describe("inbox focus and URL", () => {
  const second = {
    ...documentSummary,
    id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b5555",
    sender: "Gemeente",
  };
  const third = {
    ...documentSummary,
    id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b6666",
    sender: "Zorgverzekeraar",
  };

  async function assign(user: ReturnType<typeof userEvent.setup>, sender: string) {
    const row = (await screen.findByRole("link", { name: sender })).closest("tr");
    await user.click(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ }));
    await user.click(await screen.findByRole("option", { name: "Papa" }));
  }

  function rows(list: (typeof documentSummary)[], taken: () => string[]) {
    return http.get(`${base}/documents`, () =>
      HttpResponse.json(list.filter((document) => !taken().includes(document.id))),
    );
  }

  it("moves focus to the picker of the row that takes its place", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 3 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () => {
        taken.push(documentSummary.id);
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      rows([documentSummary, second, third], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await assign(user, "Belastingdienst");
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument(),
    );
    const next = (await screen.findByRole("link", { name: "Gemeente" })).closest("tr");
    await waitFor(() =>
      expect(
        within(next as HTMLElement).getByRole("button", { name: /Op naam van/ }),
      ).toHaveFocus(),
    );
  });

  it("moves focus to the previous row when the last row left", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 2 }),
      http.put(`${base}/documents/${second.id}/addressees`, () => {
        taken.push(second.id);
        return HttpResponse.json({ ...second, addressee_ids: [papa.id] });
      }),
      rows([documentSummary, second], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await assign(user, "Gemeente");
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Gemeente" })).not.toBeInTheDocument(),
    );
    const previous = screen.getByRole("link", { name: "Belastingdienst" }).closest("tr");
    await waitFor(() =>
      expect(
        within(previous as HTMLElement).getByRole("button", { name: /Op naam van/ }),
      ).toHaveFocus(),
    );
  });

  it("moves focus to the empty panel when the list is empty", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 1 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () => {
        taken.push(documentSummary.id);
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      rows([documentSummary], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await assign(user, "Belastingdienst");
    const note = await screen.findByText("Hier is niets te doen.");
    await waitFor(() => expect(note.closest("section")).toHaveFocus());
  });

  it("writes the chosen tab into the URL, replacing the entry", async () => {
    server.use(
      counts({ new: 0, review: 1, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      http.get(`${base}/documents`, () => HttpResponse.json([])),
    );
    const { router } = renderApp(path);
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "review" }));
    expect(router.history.length).toBe(1);
  });

  it("keeps focus on the picker and shows the error when the PUT fails", async () => {
    server.use(
      counts({ new: 0, review: 0, unassigned: 2 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () =>
        HttpResponse.json({ code: "forbidden", message: "Dat mag je niet." }, { status: 403 }),
      ),
      rows([documentSummary, second], () => []),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await assign(user, "Belastingdienst");
    // A multi-select stays open after a choice: closing it hands focus back to the picker.
    await screen.findByText("Dat mag je niet.");
    await user.keyboard("{Escape}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Dat mag je niet.");
    const row = screen.getByRole("link", { name: "Belastingdienst" }).closest("tr");
    await waitFor(() =>
      expect(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ })).toHaveFocus(),
    );
  });
});
