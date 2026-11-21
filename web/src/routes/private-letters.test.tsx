/** Private letters, sharing, who can see a letter, emergency access and the Privé
 * settings (plan Task 46, PB T22-T24). */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  asRole,
  documentDetail,
  documentHandlers,
  documentSummary,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  personHandlers,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;
const letterPath = `/w/${workspace.id}/post/${documentSummary.id}`;
const bram = "b3333333-3333-4333-8333-333333333333";

type Event = components["schemas"]["DocumentEventOut"];

const privateLetter = {
  ...documentDetail,
  visibility: "restricted",
  reader_names: ["Anne", "Bob"],
  shared_user_ids: [user.id],
};

function event(kind: string, extra: Partial<Event> = {}): Event {
  return {
    id: `e-${kind}`,
    created_at: "2026-11-20T10:00:00Z",
    kind,
    origin: "user",
    actor_name: "Carla",
    actor_id: "c4444444-4444-4444-8444-444444444444",
    rule_name: "",
    label_name: "",
    person_name: "",
    can_undo: false,
    undone: false,
    note: "",
    ...extra,
  };
}

describe("the lock", () => {
  it("a private letter in the post list shows Privé", async () => {
    server.use(
      http.get(`${base}/documents`, () =>
        HttpResponse.json([
          { ...documentSummary, visibility: "restricted", reader_names: ["Anne", "Bob"] },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    expect(within(row as HTMLElement).getByText("Privé")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Privé voor Anne en Bob")).toBeInTheDocument();
  });

  it("the letter page says who may read it", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers(privateLetter));
    renderApp(letterPath);
    expect(await screen.findByText("Privé voor Anne en Bob")).toBeInTheDocument();
  });

  it("a public letter has no lock", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(letterPath);
    await screen.findByRole("button", { name: /Op naam van/ });
    expect(screen.queryByText(/^Privé voor/)).toBeNull();
  });

  it("a letter without visibility from an old server shows no lock", async () => {
    const { visibility: _v, reader_names: _r, ...old } = documentDetail;
    server.use(...loggedInHandlers(), ...documentHandlers(old as typeof documentDetail));
    renderApp(letterPath);
    await screen.findByRole("button", { name: /Op naam van/ });
    expect(screen.queryByText("Privé")).toBeNull();
  });

  it("history tells who opened it with emergency access and why", async () => {
    server.use(
      http.get(`${base}/documents/${documentSummary.id}/events`, () =>
        HttpResponse.json([
          event("emergency_opened", { note: "Anne is in het ziekenhuis" }),
          event("made_private"),
          event("share_added", { person_name: "Bram" }),
          event("access_granted", { person_name: "Papa", note: "Dirk" }),
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(privateLetter),
    );
    renderApp(letterPath);
    expect(
      await screen.findByText("Carla opende deze brief met noodtoegang: Anne is in het ziekenhuis"),
    ).toBeInTheDocument();
    expect(screen.getByText("Carla maakte deze brief privé")).toBeInTheDocument();
    expect(screen.getByText("Carla deelde deze brief met Bram")).toBeInTheDocument();
    expect(
      screen.getByText("Dirk kan deze brief nu lezen (gekoppeld aan Papa)"),
    ).toBeInTheDocument();
  });

  it("history explains a letter that could not be made private", async () => {
    server.use(
      http.get(`${base}/documents/${documentSummary.id}/events`, () =>
        HttpResponse.json([event("private_skipped", { origin: "rule", actor_name: "" })]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(letterPath);
    expect(
      await screen.findByText(
        "Deze brief kon niet privé worden: dan zou niemand hem kunnen lezen. Hij is zichtbaar gebleven.",
      ),
    ).toBeInTheDocument();
  });
});

describe("privé maken en delen", () => {
  it("Privé maken sends restricted with the chosen members", async () => {
    let body: unknown = null;
    server.use(
      http.put(`${base}/documents/${documentSummary.id}/visibility`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ visibility: "restricted", can_read: true, reader_names: [] });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    renderApp(letterPath);
    await u.click(await screen.findByRole("button", { name: "Privé maken" }));
    const dialog = await screen.findByRole("dialog", { name: "Privé maken" });
    expect(within(dialog).getByRole("checkbox", { name: "Jij" })).toBeDisabled();
    await u.click(within(dialog).getByRole("checkbox", { name: "Bram" }));
    await u.click(within(dialog).getByRole("button", { name: "Privé maken" }));
    await waitFor(() =>
      expect(body).toEqual({ visibility: "restricted", shared_user_ids: [bram] }),
    );
  });

  it("Delen met is offered on a letter that is not private", async () => {
    let body: unknown = null;
    server.use(
      http.put(`${base}/documents/${documentSummary.id}/visibility`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ visibility: "workspace", can_read: true, reader_names: [] });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    renderApp(letterPath);
    await u.click(await screen.findByRole("button", { name: "Delen met" }));
    const dialog = await screen.findByRole("dialog", { name: "Delen met" });
    await u.click(within(dialog).getByRole("checkbox", { name: "Bram" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(body).toEqual({ visibility: "workspace", shared_user_ids: [bram] }));
  });

  it("removing yourself warns and leads back to the Inbox", async () => {
    server.use(
      http.put(`${base}/documents/${documentSummary.id}/visibility`, () =>
        HttpResponse.json({ visibility: "restricted", can_read: false, reader_names: [] }),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(privateLetter),
    );
    const u = userEvent.setup();
    renderApp(letterPath);
    await u.click(await screen.findByRole("button", { name: "Delen" }));
    const dialog = await screen.findByRole("dialog", { name: "Delen met" });
    await u.click(within(dialog).getByRole("checkbox", { name: "Jij" }));
    expect(
      within(dialog).getByText("Daarna kun je deze brief zelf misschien niet meer openen."),
    ).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inbox" })).toBeInTheDocument();
    expect(screen.getByText("Je kunt deze brief niet meer openen.")).toBeInTheDocument();
  });

  it("Niet meer privé appears only when allowed", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers(privateLetter));
    renderApp(letterPath);
    await screen.findByRole("button", { name: "Delen" });
    expect(screen.queryByRole("button", { name: "Niet meer privé" })).toBeNull();
  });

  it("the server's refusal is shown", async () => {
    server.use(
      http.put(`${base}/documents/${documentSummary.id}/visibility`, () =>
        HttpResponse.json(
          errorBody(
            "cannot_make_public",
            "Alleen wie de brief privé maakte, kan dit terugdraaien.",
          ),
          { status: 403 },
        ),
      ),
      ...loggedInHandlers(),
      ...documentHandlers({ ...privateLetter, can_make_public: true }),
    );
    const u = userEvent.setup();
    renderApp(letterPath);
    await u.click(await screen.findByRole("button", { name: "Niet meer privé" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Niet meer privé" }));
    expect(
      await within(dialog).findByText("Alleen wie de brief privé maakte, kan dit terugdraaien."),
    ).toBeInTheDocument();
  });

  it("a viewer sees no privacy buttons", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...documentHandlers());
    renderApp(letterPath);
    await screen.findByRole("button", { name: /Op naam van/ });
    expect(screen.queryByRole("button", { name: "Privé maken" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delen met" })).toBeNull();
  });

  it("removing your own addressee shows would_lose_access", async () => {
    server.use(
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () =>
        HttpResponse.json(
          errorBody("would_lose_access", "Dan kun je deze privébrief zelf niet meer lezen."),
          { status: 409 },
        ),
      ),
      ...loggedInHandlers(),
      ...documentHandlers({ ...privateLetter, addressee_ids: [papa.id] }),
    );
    const u = userEvent.setup();
    renderApp(letterPath);
    await u.click(await screen.findByRole("button", { name: /Op naam van/ }));
    await u.click(await screen.findByRole("option", { name: "Papa" }));
    expect(
      await screen.findByText("Dan kun je deze privébrief zelf niet meer lezen."),
    ).toBeInTheDocument();
  });

  it("Wie kan deze brief zien lists names with reasons", async () => {
    server.use(
      http.get(`${base}/documents/${documentSummary.id}/access`, () =>
        HttpResponse.json({
          viewers: [
            { user_id: user.id, name: "Anna", reasons: ["op naam van"] },
            { user_id: bram, name: "Bram", reasons: ["is gedeeld", "verdeelt post"] },
          ],
        }),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    renderApp(letterPath);
    await u.click(await screen.findByText("Wie kan deze brief zien?"));
    expect(await screen.findByText(/is gedeeld, verdeelt post/)).toBeInTheDocument();
    expect(screen.getByText("Bram")).toBeInTheDocument();
  });
});

describe("noodtoegang en privé-instellingen", () => {
  const settings = {
    allow_member_export: true,
    care_is_private: true,
    emergency_access: true,
    everyone_sees_all: true,
    departments_enabled: false,
    admins_read_all: true,
  };

  it("emergency access needs a reason of ten to two hundred characters", async () => {
    server.use(
      http.get(`${base}/settings`, () => HttpResponse.json(settings)),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/personen`);
    await u.click(await screen.findByRole("button", { name: "Noodtoegang: Papa" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Noodtoegang tot de post van Papa",
    });
    const open = within(dialog).getByRole("button", { name: "Openen" });
    expect(open).toBeDisabled();
    await u.type(within(dialog).getByRole("textbox", { name: "Reden" }), "te kort");
    expect(open).toBeDisabled();
    await u.type(within(dialog).getByRole("textbox", { name: "Reden" }), " en nu lang genoeg");
    expect(open).toBeEnabled();
  });

  it("emergency access reports how many letters opened", async () => {
    server.use(
      http.get(`${base}/settings`, () => HttpResponse.json(settings)),
      http.post(`${base}/persons/${papa.id}/emergency-access`, () =>
        HttpResponse.json({ opened_count: 3, expires_at: "2026-11-21T15:00:00Z" }),
      ),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/personen`);
    await u.click(await screen.findByRole("button", { name: "Noodtoegang: Papa" }));
    const dialog = await screen.findByRole("dialog");
    await u.type(
      within(dialog).getByRole("textbox", { name: "Reden" }),
      "Papa ligt in het ziekenhuis",
    );
    await u.click(within(dialog).getByRole("button", { name: "Openen" }));
    expect(
      await within(dialog).findByText("Je kunt 3 brieven een uur lang openen."),
    ).toBeInTheDocument();
  });

  it("no Noodtoegang item when the setting is off", async () => {
    server.use(
      http.get(`${base}/settings`, () =>
        HttpResponse.json({ ...settings, emergency_access: false }),
      ),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    renderApp(`/w/${workspace.id}/personen`);
    await screen.findByRole("rowheader", { name: /Papa/ });
    expect(screen.queryByRole("button", { name: "Noodtoegang: Papa" })).toBeNull();
  });

  it("an admin switches Zorg-post is privé", async () => {
    let body: unknown = null;
    server.use(
      http.get(`${base}/settings`, () => HttpResponse.json(settings)),
      http.put(`${base}/settings`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...settings, care_is_private: false });
      }),
      ...loggedInHandlers(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/instellingen`);
    await u.click(await screen.findByRole("switch", { name: "Zorg-post is privé" }));
    await waitFor(() => expect(body).toEqual({ care_is_private: false }));
  });

  it("only a server admin can switch emergency access", async () => {
    server.use(
      http.get(`${base}/settings`, () => HttpResponse.json(settings)),
      ...loggedInHandlers(),
    );
    renderApp(`/w/${workspace.id}/instellingen`);
    expect(
      await screen.findByText(
        "Noodtoegang voor de beheerder staat aan. Alleen de serverbeheerder kan dit veranderen.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Noodtoegang voor de beheerder" })).toBeNull();
  });

  it("a server admin switches emergency access", async () => {
    let body: unknown = null;
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json({ ...user, is_server_admin: true })),
      http.get(`${base}/settings`, () => HttpResponse.json(settings)),
      http.put(`${base}/emergency-access`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ enabled: false });
      }),
      ...loggedInHandlers(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/instellingen`);
    await u.click(await screen.findByRole("switch", { name: "Noodtoegang voor de beheerder" }));
    await waitFor(() => expect(body).toEqual({ enabled: false }));
  });
});
