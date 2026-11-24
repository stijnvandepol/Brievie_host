import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  belasting,
  documentHandlers,
  documentSummary,
  errorBody,
  gate,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  renderApp,
  secondWorkspace,
  server,
  twoWorkspaces,
  workspace,
} from "../test-utils";

type Out = components["schemas"]["DocumentSearchOut"];
type Hit = components["schemas"]["DocumentSearchHitOut"];

const hit = (over: Partial<Hit> = {}): Hit => ({
  ...documentSummary,
  snippet: [
    { text: "Betaal vóór ", hit: false },
    { text: "1 december", hit: true },
    { text: " de aanslag.", hit: false },
  ],
  ...over,
});

const answer = (over: Partial<Out> = {}): Out => ({
  items: [hit()],
  next_offset: null,
  fuzzy: false,
  bsn_removed: false,
  ...over,
});

/** Answers every search with `reply(body, call)` and records the bodies. */
function searchHandler(reply: (body: Record<string, unknown>, call: number) => Out | Response) {
  const bodies: Record<string, unknown>[] = [];
  const handler = http.post(
    `*${API}/workspaces/${workspace.id}/documents/search`,
    async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      bodies.push(body);
      const out = reply(body, bodies.length);
      return out instanceof Response ? out : HttpResponse.json(out);
    },
  );
  return { bodies, handler };
}

async function searchFor(term: string) {
  const u = userEvent.setup();
  const field = await screen.findByRole("searchbox", { name: "Zoeken in je post" });
  await u.click(field);
  await u.type(field, `${term}{Enter}`);
  await screen.findByRole("heading", { level: 1, name: "Zoeken" });
  return u;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** A phone: every `max-width` query matches, every `min-width` query does not. */
function phoneScreen() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

const notLoggedIn = () =>
  HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 });

describe("search field", () => {
  it("is in the app bar with its name and placeholder", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/archief`);
    const field = await screen.findByRole("searchbox", { name: "Zoeken in je post" });
    expect(field).toHaveAttribute("placeholder", "Zoek op afzender, onderwerp of tekst");
  });

  it("is focused by / from anywhere", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/archief`);
    const field = await screen.findByRole("searchbox", { name: "Zoeken in je post" });
    await u.keyboard("/");
    expect(field).toHaveFocus();
    expect(field).toHaveValue("");
  });

  it("does not take / while typing in another field", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/archief`);
    const field = await screen.findByRole("searchbox", { name: "Zoeken in je post" });
    const select = await screen.findByLabelText("Van wie");
    await u.click(select);
    await u.keyboard("/");
    expect(field).not.toHaveFocus();
    expect(select).toHaveFocus();
  });

  it("clears on Escape and gives focus back to where it was", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/archief`);
    const field = await screen.findByRole("searchbox", { name: "Zoeken in je post" });
    const select = await screen.findByLabelText("Van wie");
    select.focus();
    await u.keyboard("/");
    await u.type(field, "abc");
    expect(field).toHaveValue("abc");
    await u.keyboard("{Escape}");
    expect(field).toHaveValue("");
    expect(select).toHaveFocus();
  });

  it("goes to the results with the term in the POST body and not in the URL", async () => {
    const { bodies, handler } = searchHandler(() => answer());
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting");
    await screen.findByRole("heading", { level: 1, name: "Zoeken" });
    await screen.findByRole("link", { name: "Belastingdienst" });
    expect(bodies).toHaveLength(1);
    expect(bodies[0]?.q).toBe("belasting");
    expect(router.state.location.pathname).toBe(`/w/${workspace.id}/zoeken`);
    expect(router.state.location.href).not.toContain("belasting");
    expect(JSON.stringify(window.history.state ?? {})).not.toContain("belasting");
    expect(window.location.href).not.toContain("belasting");
  });
});

describe("forgetting the term", () => {
  it("forgets the term and the results after signing out", async () => {
    const { bodies, handler } = searchHandler(() => answer());
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      handler,
      http.post(`*${API}/auth/logout`, () => {
        server.use(http.get(`*${API}/me`, notLoggedIn));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    const u = await searchFor("belasting");
    await screen.findByRole("link", { name: "Belastingdienst" });
    await u.click(screen.getByRole("button", { name: "Account" }));
    await u.click(await screen.findByRole("button", { name: /uitloggen/i }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));

    // Somebody else signs in on the same computer and goes back to the results page.
    server.use(...loggedInHandlers());
    await router.navigate({ to: "/w/$workspaceId/zoeken", params: { workspaceId: workspace.id } });
    expect(await screen.findByRole("heading", { name: "Zoek een brief" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Zoeken in je post" })).toHaveValue("");
    expect(bodies).toHaveLength(1);
  });

  it("forgets the term when switching to another workspace", async () => {
    const { bodies, handler } = searchHandler(() => answer());
    const other: Record<string, unknown>[] = [];
    server.use(
      twoWorkspaces(),
      ...loggedInHandlers(),
      ...documentHandlers(),
      handler,
      http.get(`*${API}/workspaces/${secondWorkspace.id}/inbox/counts`, () =>
        HttpResponse.json({ new: 0, review: 0, unassigned: 0 }),
      ),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/labels`, () => HttpResponse.json([])),
      http.get(`*${API}/workspaces/${secondWorkspace.id}/persons`, () => HttpResponse.json([])),
      http.post(
        `*${API}/workspaces/${secondWorkspace.id}/documents/search`,
        async ({ request }) => {
          other.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json(answer());
        },
      ),
    );
    const { router } = renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting");
    await screen.findByRole("link", { name: "Belastingdienst" });

    await router.navigate({
      to: "/w/$workspaceId/zoeken",
      params: { workspaceId: secondWorkspace.id },
    });
    expect(await screen.findByRole("heading", { name: "Zoek een brief" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Zoeken in je post" })).toHaveValue("");
    expect(bodies).toHaveLength(1);
    expect(other).toHaveLength(0);
  });
});

describe("search field on a phone", () => {
  it("opens over the bar with a close button", async () => {
    phoneScreen();
    server.use(...loggedInHandlers(), ...documentHandlers());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/archief`);
    await u.click(await screen.findByRole("button", { name: "Zoeken" }));
    expect(screen.getByRole("searchbox", { name: "Zoeken in je post" })).toHaveFocus();
    await u.type(screen.getByRole("searchbox", { name: "Zoeken in je post" }), "abc");
    await u.click(screen.getByRole("button", { name: "Sluiten" }));
    expect(screen.queryByRole("searchbox", { name: "Zoeken in je post" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoeken" })).toBeInTheDocument();
  });

  it("closes after a search, also on the results page", async () => {
    phoneScreen();
    const { bodies, handler } = searchHandler(() => answer());
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    const u = userEvent.setup();
    // On the results page a search draws no new page, so the field must close itself.
    renderApp(`/w/${workspace.id}/zoeken`);
    await u.click(await screen.findByRole("button", { name: "Zoeken" }));
    await u.type(screen.getByRole("searchbox", { name: "Zoeken in je post" }), "belasting{Enter}");
    await screen.findByRole("heading", { level: 1, name: "Zoeken" });
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(screen.queryByRole("searchbox", { name: "Zoeken in je post" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoeken" })).toBeInTheDocument();
  });
});

describe("results page", () => {
  it("shows the start state after a reload", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/zoeken`);
    expect(await screen.findByRole("heading", { name: "Zoek een brief" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Zoek op afzender, onderwerp, een woord uit de brief, een label of een naam. Het begin van een woord is genoeg.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Zoeken - Brievie"));
  });

  it("marks hits and keeps text that looks like HTML as text", async () => {
    const { handler } = searchHandler(() =>
      answer({
        items: [
          hit({
            snippet: [
              { text: "<b>vet</b> ", hit: false },
              { text: "<img src=x onerror=alert(1)>", hit: true },
            ],
          }),
        ],
      }),
    );
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("vet");
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest(
      "tr",
    ) as HTMLElement;
    const marks = row.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(row.querySelector("b")).toBeNull();
    expect(row.querySelector("img")).toBeNull();
    expect(row).toHaveTextContent("<b>vet</b>");
  });

  it("marks the hit of a normal fragment", async () => {
    const { handler } = searchHandler(() => answer());
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("december");
    const mark = await screen.findByText("1 december");
    expect(mark.tagName).toBe("MARK");
    expect(await screen.findByText("1 brief gevonden")).toBeInTheDocument();
  });

  it("announces searching in a polite live region", async () => {
    const hold = gate();
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(`*${API}/workspaces/${workspace.id}/documents/search`, async () => {
        await hold.opened;
        return HttpResponse.json(answer());
      }),
    );
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting");
    const status = await screen.findByText("Bezig met zoeken…");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("role", "status");
    hold.open();
    expect(await screen.findByText("1 brief gevonden")).toBeInTheDocument();
  });

  it("counts several letters", async () => {
    const { handler } = searchHandler(() =>
      answer({ items: [hit(), hit({ id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4445" })] }),
    );
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting");
    expect(await screen.findByText("2 brieven gevonden")).toBeInTheDocument();
  });

  it("says when an answer is approximate", async () => {
    const { handler } = searchHandler(() => answer({ fuzzy: true }));
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belastingdeinst");
    expect(await screen.findByText("Geen exacte treffer. Dit lijkt erop.")).toBeInTheDocument();
  });

  it("says a BSN was removed from the term", async () => {
    const { handler } = searchHandler(() => answer({ bsn_removed: true }));
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting 111222333");
    expect(
      await screen.findByText(
        "Zoeken op een BSN kan niet: Brievie bewaart geen BSN's. We hebben het nummer uit je zoekopdracht gehaald.",
      ),
    ).toBeInTheDocument();
  });

  it("offers next steps when nothing is found", async () => {
    const { handler } = searchHandler(() => answer({ items: [] }));
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("zzzz");
    expect(await screen.findByRole("heading", { name: "Niets gevonden" })).toBeInTheDocument();
    expect(screen.getByText("Gebruik minder woorden.")).toBeInTheDocument();
    expect(
      screen.getByText("Probeer een andere spelling of het begin van het woord."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Bekijk het archief" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/archief`,
    );
    expect(screen.queryByRole("button", { name: "Wis filters" })).not.toBeInTheDocument();
  });

  it("does not call the API for a term of one character", async () => {
    const { bodies, handler } = searchHandler(() => answer());
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("a");
    expect(await screen.findByText("Typ minstens twee tekens.")).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
  });

  it("shows the Dutch message of an error, also a throttled one", async () => {
    const { handler } = searchHandler(() =>
      HttpResponse.json(
        errorBody("throttled", "Je zoekt te vaak. Wacht even en probeer opnieuw."),
        {
          status: 429,
        },
      ),
    );
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Je zoekt te vaak. Wacht even en probeer opnieuw.",
    );
  });
});

describe("filters and paging", () => {
  it("sends the filters in the body and offers to clear them", async () => {
    const { bodies, handler } = searchHandler(() => answer({ items: [] }));
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    const u = await searchFor("belasting");
    await screen.findByRole("heading", { name: "Niets gevonden" });
    expect(screen.queryByRole("button", { name: "Wis filters" })).not.toBeInTheDocument();

    await u.selectOptions(screen.getByLabelText("Soort"), "Aanslag");
    await u.selectOptions(await screen.findByLabelText("Label"), "Belastingdienst");
    await u.selectOptions(screen.getByLabelText("Persoon"), "Papa");
    await u.selectOptions(screen.getByLabelText("Status"), "Naam kiezen");
    await u.type(screen.getByLabelText("Van"), "2026-01-01");
    await u.type(screen.getByLabelText("Tot"), "2026-12-31");

    await waitFor(() => expect(bodies.at(-1)?.date_to).toBe("2026-12-31"));
    expect(bodies.at(-1)).toMatchObject({
      q: "belasting",
      document_type: "assessment",
      label_id: belasting.id,
      person_id: papa.id,
      inbox_state: "unassigned",
      date_from: "2026-01-01",
      date_to: "2026-12-31",
      offset: 0,
    });
    expect(screen.getByRole("group", { name: "Periode" })).toBeInTheDocument();

    const count = bodies.length;
    const buttons = screen.getAllByRole("button", { name: "Wis filters" });
    await u.click(buttons[0] as HTMLElement);
    await waitFor(() => expect(bodies.length).toBeGreaterThan(count));
    expect(bodies.at(-1)).toMatchObject({ document_type: "", inbox_state: "", label_id: null });
    expect(screen.queryByRole("button", { name: "Wis filters" })).not.toBeInTheDocument();
  });

  it("waits until the filters stop changing and then searches once", async () => {
    const { bodies, handler } = searchHandler(() => answer({ items: [] }));
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    await searchFor("belasting");
    await screen.findByRole("heading", { name: "Niets gevonden" });
    expect(bodies).toHaveLength(1);

    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const from = screen.getByLabelText("Van");
    // Paging through days with the arrow keys: a change every few milliseconds.
    for (const day of ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]) {
      fireEvent.change(from, { target: { value: day } });
      vi.advanceTimersByTime(100);
    }
    fireEvent.change(screen.getByLabelText("Soort"), { target: { value: "assessment" } });
    vi.advanceTimersByTime(299);
    expect(bodies).toHaveLength(1);
    vi.advanceTimersByTime(1);
    vi.useRealTimers();

    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toMatchObject({ date_from: "2026-01-04", document_type: "assessment" });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(bodies).toHaveLength(2);
  });

  it("loads more, appends, and stops when there is no next page", async () => {
    const second = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4445";
    const { bodies, handler } = searchHandler((body) =>
      body.offset === 0
        ? answer({ items: [hit()], next_offset: 30 })
        : answer({ items: [hit({ id: second, sender: "Gemeente" })], next_offset: null }),
    );
    server.use(...loggedInHandlers(), ...documentHandlers(), handler);
    renderApp(`/w/${workspace.id}/archief`);
    const u = await searchFor("brief");
    expect(await screen.findByText("1 brief geladen, er zijn er meer")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Meer laden" }));
    expect(await screen.findByRole("link", { name: "Gemeente" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Belastingdienst" })).toBeInTheDocument();
    expect(bodies[1]).toMatchObject({ q: "brief", offset: 30 });
    expect(screen.queryByRole("button", { name: "Meer laden" })).not.toBeInTheDocument();
    expect(screen.getByText("2 brieven gevonden")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });
});
