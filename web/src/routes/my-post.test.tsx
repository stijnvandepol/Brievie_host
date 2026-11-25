import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  documentHandlers,
  documentSummary,
  HttpResponse,
  homeVisibility,
  http,
  loggedInHandlers,
  menuCounts,
  renderApp,
  server,
  workspace,
} from "../test-utils";

type Summary = components["schemas"]["DocumentSummaryOut"];

const path = `/w/${workspace.id}/mijn-post`;
const base = `*${API}/workspaces/${workspace.id}`;

const unreadLetter: Summary = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b5555",
  sender: "Gemeente",
  unread: true,
};
const readLetter: Summary = { ...documentSummary, sender: "Belastingdienst", unread: false };

/** Answers Mijn post's list and remembers what was asked. */
function myPost(asked: URLSearchParams[], list: Summary[] = [unreadLetter, readLetter]) {
  return http.get(`${base}/documents`, ({ request }) => {
    const params = new URL(request.url).searchParams;
    asked.push(params);
    const unreadOnly = params.get("unread_only") === "true";
    return HttpResponse.json(unreadOnly ? list.filter((d) => d.unread) : list);
  });
}

function rowOf(sender: string): HTMLElement {
  return screen.getByRole("link", { name: sender }).closest("tr") as HTMLElement;
}

describe("Mijn post", () => {
  it("lists my letters, the unread ones marked", async () => {
    const asked: URLSearchParams[] = [];
    server.use(myPost(asked), ...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Mijn post" })).toBeVisible();
    await screen.findByRole("link", { name: "Gemeente" });
    expect(asked[0]?.get("addressed_to_me")).toBe("true");
    expect(within(rowOf("Gemeente")).getByRole("rowheader")).toHaveTextContent(/Ongelezen/);
    expect(rowOf("Gemeente")).toHaveClass("unread");
    expect(within(rowOf("Belastingdienst")).queryByText(/Ongelezen/)).not.toBeInTheDocument();
    expect(rowOf("Belastingdienst")).not.toHaveClass("unread");
  });

  it("Alleen ongelezen filters and Alles gelezen marks the rows on screen", async () => {
    const asked: URLSearchParams[] = [];
    let marked: unknown = null;
    server.use(
      myPost(asked),
      http.post(`${base}/documents/read`, async ({ request }) => {
        marked = await request.json();
        return HttpResponse.json({ marked: 1 });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    const { router } = renderApp(path);
    await screen.findByRole("link", { name: "Gemeente" });

    await u.click(screen.getByRole("checkbox", { name: "Alleen ongelezen" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ ongelezen: true }));
    await waitFor(() => expect(asked.at(-1)?.get("unread_only")).toBe("true"));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument(),
    );

    await u.click(screen.getByRole("button", { name: "Alles gelezen" }));
    await waitFor(() => expect(marked).toEqual({ document_ids: [unreadLetter.id] }));
  });

  it("has nothing to mark when everything is read", async () => {
    server.use(myPost([], [readLetter]), ...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    await screen.findByRole("link", { name: "Belastingdienst" });
    expect(screen.getByRole("button", { name: "Alles gelezen" })).toBeDisabled();
  });

  it("a member without a person is told why the list is empty", async () => {
    server.use(
      myPost([], []),
      menuCounts({ has_person: false }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(
      await screen.findByText(/Je account hoort nog bij geen persoon in deze werkruimte/),
    ).toBeVisible();
    expect(screen.getByText("Er is nog geen post op jouw naam.")).toBeVisible();
  });

  it("offers Ook mijn afdeling where departments are on", async () => {
    const asked: URLSearchParams[] = [];
    server.use(
      myPost(asked),
      http.get(`${base}/visibility`, () =>
        HttpResponse.json({ ...homeVisibility, kind: "business", departments_enabled: true }),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("checkbox", { name: "Ook mijn afdeling" }));
    await waitFor(() => expect(asked.at(-1)?.get("include_departments")).toBe("true"));
  });

  it("calls a home's departments groups, and has no filter without them", async () => {
    server.use(myPost([]), ...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    await screen.findByRole("link", { name: "Gemeente" });
    expect(screen.queryByRole("checkbox", { name: /Ook mijn/ })).not.toBeInTheDocument();
  });
});

describe("unread elsewhere", () => {
  it("the Inbox rows carry no unread mark", async () => {
    server.use(
      http.get(`${base}/documents`, ({ request }) => {
        const state = new URL(request.url).searchParams.get("inbox_state");
        return HttpResponse.json(
          state === "unassigned" ? [{ ...unreadLetter, inbox_state: "unassigned" }] : [],
        );
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/inbox`);
    await screen.findByRole("link", { name: "Gemeente" });
    expect(screen.queryByText(/Ongelezen/)).not.toBeInTheDocument();
  });

  it("Archief shows no unread marks: those are for Mijn post", async () => {
    server.use(
      http.post(`${base}/documents/archive`, () => HttpResponse.json([unreadLetter, readLetter])),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/archief`);
    await screen.findByRole("link", { name: "Gemeente" });
    expect(rowOf("Gemeente")).not.toHaveClass("unread");
    expect(screen.queryByRole("checkbox", { name: "Alleen ongelezen" })).not.toBeInTheDocument();
  });
});
