import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  asRole,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  members,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/instellingen/wie-ziet-wat`;
const base = `*${API}/workspaces/${workspace.id}`;

type Visibility = components["schemas"]["VisibilityOut"];
type Preview = components["schemas"]["VisibilityPreviewOut"];

const ownPost: Visibility = {
  kind: "business",
  everyone_sees_all: false,
  departments_enabled: true,
  admins_read_all: false,
  sorter_user_ids: [],
  sentences: ["Je ziet post op je eigen naam."],
  summary: ["Iedereen ziet zijn eigen post en die van zijn afdelingen."],
};

const everyone: Visibility = {
  ...ownPost,
  kind: "home",
  everyone_sees_all: true,
  departments_enabled: false,
  admins_read_all: true,
  summary: ["Iedereen in Brievie ziet alle post, behalve privé-post."],
};

const settings = {
  allow_member_export: false,
  care_is_private: false,
  emergency_access: true,
  everyone_sees_all: false,
  departments_enabled: true,
  admins_read_all: false,
  summary_language: "nl",
};

function gainingPreview(): Preview {
  return {
    changes: ["Anna", "Bram", "Carla", "Dirk", "Eva"].map((name, index) => ({
      user_id: `c000000${index}-0000-4000-8000-000000000000`,
      name,
      gains: ["alle post die niet privé is"],
      losses: [],
    })),
    letter_count: 12,
    needs_confirmation: true,
    sentences: ["Iedereen in Brievie ziet alle post, behalve privé-post."],
    preview_token: "token-1",
  };
}

function handlers(visibility: Visibility = ownPost, role: "admin" | "member" | "viewer" = "admin") {
  return [
    asRole(role, visibility.kind === "home" ? "home" : "business"),
    http.get(`${base}/visibility`, () => HttpResponse.json(visibility)),
    http.get(`${base}/settings`, () => HttpResponse.json(settings)),
    http.get(`${base}/members`, () => HttpResponse.json(members)),
    ...loggedInHandlers(),
  ];
}

describe("wie ziet wat", () => {
  it("an admin sees the three switches with their sentences", async () => {
    server.use(...handlers());
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Wie ziet wat" })).toBeVisible();
    expect(
      await screen.findByText("Iedereen ziet zijn eigen post en die van zijn afdelingen."),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Iedereen ziet alle post" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Afdelingen gebruiken" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Beheerders lezen alle post" })).not.toBeChecked();
    expect(
      screen.getByText(
        "Aan: iedereen in Brievie ziet elke brief, behalve brieven die privé zijn. Uit: je ziet alleen post op je eigen naam en van je afdelingen.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Aan: Anna zien alle post die niet privé is. Uit: zij regelen wie wat ziet, maar lezen alleen post die voor hen is.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Dit bepaalt welke standaard Brievie voorstelt. Het verandert zelf niemands rechten.",
      ),
    ).toBeInTheDocument();
    const unnamed = screen.getByRole("region", { name: "Post zonder naam" });
    expect(
      within(unnamed).getByText(
        "Wie hier staat, ziet brieven waar nog geen naam of afdeling op staat, en zet er een naam op. Staat hier niemand, dan doen de beheerders dat: Anna.",
      ),
    ).toBeInTheDocument();
  });

  it("Beheerders lezen alle post and Post zonder naam hide while Iedereen ziet alle post is on", async () => {
    server.use(...handlers({ ...ownPost, everyone_sees_all: true }));
    renderApp(path);
    expect(await screen.findByRole("switch", { name: "Iedereen ziet alle post" })).toBeChecked();
    expect(screen.queryByRole("switch", { name: "Beheerders lezen alle post" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Post zonder naam" })).toBeNull();
    expect(
      screen.getByText(
        "Nu zien nog steeds alle mensen alle post; afdelingen helpen dan alleen bij het sorteren.",
      ),
    ).toBeInTheDocument();
  });

  it("turning Iedereen ziet alle post on asks for confirmation", async () => {
    let previewed: unknown = null;
    let saved: unknown = null;
    server.use(
      ...handlers(),
      http.post(`${base}/visibility/preview`, async ({ request }) => {
        previewed = await request.json();
        return HttpResponse.json(gainingPreview());
      }),
      http.put(`${base}/visibility`, async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ ...ownPost, everyone_sees_all: true });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("switch", { name: "Iedereen ziet alle post" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(
      within(dialog).getByRole("heading", { name: "Dit verandert voor 5 mensen wat ze zien." }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Gaan meer zien" })).toHaveTextContent(
      "Bram: alle post die niet privé is",
    );
    expect(saved).toBeNull();
    await u.click(within(dialog).getByRole("button", { name: "Ja, iedereen mag alle post zien" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(previewed).toMatchObject({ everyone_sees_all: true, member_change: "" });
    expect(saved).toEqual({
      add_start_set: false,
      apply_preset: false,
      everyone_sees_all: true,
      confirmed: true,
      preview_token: "token-1",
    });
  });

  it("after a change, the workspace's letters and counts are asked again", async () => {
    let counted = 0;
    server.use(
      http.get(`${base}/inbox/counts`, () => {
        counted += 1;
        return HttpResponse.json({ new: 0, review: 0, unassigned: 0 });
      }),
      ...handlers({ ...ownPost, everyone_sees_all: true }),
      http.post(`${base}/visibility/preview`, () =>
        HttpResponse.json({ ...gainingPreview(), needs_confirmation: false }),
      ),
      http.put(`${base}/visibility`, () => HttpResponse.json(ownPost)),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("switch", { name: "Iedereen ziet alle post" }));
    await waitFor(() => expect(counted).toBeGreaterThan(0));
    const before = counted;
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Wijzigen" }));
    await waitFor(() => expect(counted).toBeGreaterThan(before));
  });

  it("turning it off shows who sees less and needs no Ja-button", async () => {
    let saved: unknown = null;
    server.use(
      ...handlers({ ...ownPost, everyone_sees_all: true }),
      http.post(`${base}/visibility/preview`, () =>
        HttpResponse.json({
          changes: [
            {
              user_id: "b3333333-3333-4333-8333-333333333333",
              name: "Bram",
              gains: [],
              losses: ["alle post die niet privé is"],
            },
          ],
          letter_count: 3,
          needs_confirmation: false,
          sentences: [],
          preview_token: "token-2",
        } satisfies Preview),
      ),
      http.put(`${base}/visibility`, async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json(ownPost);
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("switch", { name: "Iedereen ziet alle post" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("region", { name: "Gaan minder zien" })).toHaveTextContent(
      "Bram: alle post die niet privé is",
    );
    expect(within(dialog).queryByRole("button", { name: /^Ja/ })).toBeNull();
    await u.click(within(dialog).getByRole("button", { name: "Wijzigen" }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toMatchObject({ everyone_sees_all: false });
  });

  it("a stale preview is shown again with the server's sentence", async () => {
    let previews = 0;
    let puts = 0;
    server.use(
      ...handlers(),
      http.post(`${base}/visibility/preview`, () => {
        previews += 1;
        return HttpResponse.json({ ...gainingPreview(), preview_token: `token-${previews}` });
      }),
      http.put(`${base}/visibility`, async ({ request }) => {
        puts += 1;
        const body = (await request.json()) as { preview_token: string };
        if (body.preview_token === "token-1") {
          return HttpResponse.json(
            errorBody(
              "preview_outdated",
              "Er is intussen iets veranderd. Bekijk de wijziging opnieuw.",
            ),
            { status: 409 },
          );
        }
        return HttpResponse.json({ ...ownPost, everyone_sees_all: true });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("switch", { name: "Iedereen ziet alle post" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Ja, iedereen mag alle post zien" }));
    expect(
      await within(dialog).findByText(
        "Er is intussen iets veranderd. Bekijk de wijziging opnieuw.",
      ),
    ).toBeInTheDocument();
    expect(previews).toBe(2);
    await u.click(within(dialog).getByRole("button", { name: "Ja, iedereen mag alle post zien" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(puts).toBe(2);
  });

  it("switching to Thuis offers the preset values as checkboxes", async () => {
    let previewed: unknown = null;
    server.use(
      ...handlers(),
      http.post(`${base}/visibility/preview`, async ({ request }) => {
        previewed = await request.json();
        return HttpResponse.json(gainingPreview());
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("radio", { name: "Thuis" }));
    const dialog = await screen.findByRole("dialog", { name: "Soort gebruik: Thuis" });
    const everyoneBox = within(dialog).getByRole("checkbox", {
      name: "Iedereen ziet alle post: aan",
    });
    expect(everyoneBox).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "Groepen gebruiken: uit" })).toBeChecked();
    expect(
      within(dialog).getByRole("checkbox", { name: "Beheerders lezen alle post: aan" }),
    ).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "Zorg-post is privé: aan" })).toBeChecked();
    expect(
      within(dialog).getByRole("checkbox", {
        name: "Leden mogen hun eigen post exporteren: aan",
      }),
    ).toBeChecked();
    expect(
      within(dialog).getByText("De serverbeheerder zet noodtoegang aan of uit."),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("checkbox", { name: "Ook de startset labels toevoegen" }),
    ).not.toBeChecked();
    // Keep departments as they are: the current value is sent, not left to the preset.
    await u.click(within(dialog).getByRole("checkbox", { name: "Groepen gebruiken: uit" }));
    await u.click(within(dialog).getByRole("button", { name: "Verder" }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(previewed).toMatchObject({
      kind: "home",
      apply_preset: true,
      add_start_set: false,
      departments_enabled: true,
    });
    expect(previewed).not.toHaveProperty("everyone_sees_all");
  });

  it("a member is redirected to Inbox", async () => {
    server.use(...handlers(ownPost, "member"));
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Inbox" })).toBeInTheDocument();
  });

  it("a Thuis workspace says Groepen gebruiken", async () => {
    server.use(...handlers(everyone));
    renderApp(path);
    expect(await screen.findByRole("switch", { name: "Groepen gebruiken" })).not.toBeChecked();
    expect(screen.queryByRole("switch", { name: "Afdelingen gebruiken" })).toBeNull();
  });

  it("is linked from Instellingen", async () => {
    server.use(...handlers());
    renderApp(`/w/${workspace.id}/instellingen`);
    expect(await screen.findByRole("link", { name: "Wie ziet wat" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/instellingen/wie-ziet-wat`,
    );
  });
});
