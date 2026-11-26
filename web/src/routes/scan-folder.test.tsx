import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/instellingen/scanmap`;
const settingsPath = `/w/${workspace.id}/instellingen`;
const base = `*${API}/workspaces/${workspace.id}`;

type Body = Record<string, unknown>;

const off = {
  available: true,
  enabled: false,
  folder_name: "",
  suggested_name: "thuis",
  per_person: false,
  check: "unknown",
  owner_uid: 0,
  owner_gid: 0,
  waiting_count: 0,
  oldest_waiting_minutes: 0,
  failed: [],
  folders: [],
  unknown_folders: [],
  departments_enabled: true,
};

const on = { ...off, enabled: true, folder_name: "thuis", check: "ok" };

const persons = [
  {
    id: "p-lid",
    display_name: "Lid",
    kind: "person",
    archived: false,
    document_count: 0,
    name_variants: [],
    user_id: "u-lid",
  },
  {
    id: "p-afd",
    display_name: "Financien",
    kind: "department",
    archived: false,
    document_count: 0,
    name_variants: [],
    user_id: null,
  },
];

function scanServer(initial: Body = off) {
  const state = {
    current: initial,
    puts: [] as Body[],
    posts: [] as Body[],
    deletes: [] as string[],
  };
  server.use(
    ...loggedInHandlers(),
    http.get(`${base}/scan-folder`, () => HttpResponse.json(state.current)),
    http.get(`${base}/persons`, () => HttpResponse.json(persons)),
    http.get(`${base}/settings`, () => HttpResponse.json({ allow_member_export: true })),
    http.put(`${base}/scan-folder`, async ({ request }) => {
      const body = (await request.json()) as Body;
      state.puts.push(body);
      state.current = { ...state.current, ...body, folder_name: body.folder_name || "thuis" };
      return HttpResponse.json(state.current);
    }),
    http.post(`${base}/scan-folder/folders`, async ({ request }) => {
      const body = (await request.json()) as Body;
      state.posts.push(body);
      return HttpResponse.json(state.current);
    }),
    http.delete(`${base}/scan-folder/folders/:folderId`, ({ params }) => {
      state.deletes.push(String(params.folderId));
      return HttpResponse.json(state.current);
    }),
  );
  return state;
}

describe("Scanmap", () => {
  it("is offered to an admin on the settings page when the server has a folder", async () => {
    scanServer();
    renderApp(settingsPath);
    expect(await screen.findByRole("link", { name: "Scanmap" })).toHaveAttribute("href", path);
  });

  it("is not offered when the server has no scan folder", async () => {
    scanServer({ ...off, available: false });
    renderApp(settingsPath);
    expect(await screen.findByRole("link", { name: "Logboek" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Scanmap" })).not.toBeInTheDocument();
  });

  it("is not offered to a member", async () => {
    scanServer();
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "member" }])),
    );
    renderApp(settingsPath);
    expect(await screen.findByRole("link", { name: "Wie kan mijn post zien?" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Scanmap" })).not.toBeInTheDocument();
  });

  it("switches on with the suggested name", async () => {
    const state = scanServer();
    const user = userEvent.setup();
    renderApp(path);
    expect(await screen.findByRole("textbox", { name: "Naam van de map" })).toHaveValue("thuis");
    expect(screen.getByText(/Zet je scanner op één bestand per brief\./)).toBeVisible();
    expect(screen.getByText(/staat het in Brievie, niet in de prullenbak/)).toBeVisible();
    await user.click(screen.getByRole("switch", { name: "Scanmap aan" }));
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByText("Opgeslagen.")).toBeVisible();
    expect(state.puts).toEqual([{ enabled: true, folder_name: "thuis", per_person: false }]);
  });

  it("shows the server's sentence when the name is taken", async () => {
    scanServer();
    server.use(
      http.put(`${base}/scan-folder`, () =>
        HttpResponse.json(
          errorBody(
            "scan_folder_taken",
            "Een andere werkruimte gebruikt deze mapnaam al. Kies een andere naam.",
          ),
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("switch", { name: "Scanmap aan" }));
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Een andere werkruimte gebruikt deze mapnaam al. Kies een andere naam.",
    );
  });

  it("says when the folder cannot be written", async () => {
    scanServer({ ...on, check: "not_writable", owner_uid: 1026, owner_gid: 100 });
    renderApp(path);
    expect(
      await screen.findByText(
        "Brievie kan de scanmap niet lezen. Geef de groep 'users' lees- en schrijfrechten op deze map.",
      ),
    ).toBeVisible();
    expect(screen.getByText(/1026/)).toBeVisible();
  });

  it("says when the folder belongs to another workspace", async () => {
    scanServer({ ...on, check: "taken" });
    renderApp(path);
    expect(await screen.findByText(/hoort bij een andere werkruimte/)).toBeVisible();
  });

  it("says when it works", async () => {
    scanServer(on);
    renderApp(path);
    expect(await screen.findByText("De scanmap werkt.")).toBeVisible();
  });

  it("warns about files that wait for more than an hour", async () => {
    scanServer({ ...on, waiting_count: 4, oldest_waiting_minutes: 75 });
    renderApp(path);
    expect(
      await screen.findByText("Er wachten 4 bestanden langer dan een uur. Draait Brievie?"),
    ).toBeVisible();
  });

  it("lists failed files with their reason and no action", async () => {
    scanServer({
      ...on,
      failed: [
        { name: "beveiligd.pdf", reason: "file_encrypted", failed_at: "2026-11-25T10:00:00Z" },
      ],
    });
    renderApp(path);
    const table = await screen.findByRole("table", { name: "Mislukt" });
    const row = within(table).getByRole("row", { name: /beveiligd\.pdf/ });
    expect(within(row).getByText("Beveiligd met een wachtwoord")).toBeVisible();
    expect(within(row).getByText(/2026/)).toBeVisible();
    expect(within(table).queryByRole("button")).not.toBeInTheDocument();
  });

  it("lists the folders per person and department", async () => {
    const state = scanServer({
      ...on,
      per_person: true,
      folders: [
        {
          id: "f-afd",
          name: "Financien",
          person_id: "p-afd",
          person_name: "Financien",
          kind: "department",
          state: "paused",
          waiting_count: 2,
        },
      ],
    });
    const user = userEvent.setup();
    renderApp(path);
    expect(
      await screen.findByRole("switch", { name: "Mappen per persoon en afdeling" }),
    ).toBeChecked();
    const folders = screen.getByRole("table", { name: "Mappen" });
    const row = within(folders).getByRole("row", { name: /Financien/ });
    expect(within(row).getByText("Wacht: afdelingen staan uit")).toBeVisible();
    expect(screen.getByText(/volgt .*Wie ziet wat/)).toBeVisible();

    await user.click(await screen.findByRole("button", { name: "Map maken: Lid" }));
    expect(state.posts).toEqual([{ person_id: "p-lid", name: "" }]);

    await user.click(within(row).getByRole("button", { name: "Niet meer lezen: Financien" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/blijven op de NAS staan/)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Niet meer lezen" }));
    expect(state.deletes).toEqual(["f-afd"]);
  });

  it("offers to link an unknown folder", async () => {
    const state = scanServer({
      ...on,
      per_person: true,
      unknown_folders: [{ name: "Oude scans", files: 3 }],
    });
    const user = userEvent.setup();
    renderApp(path);
    expect(
      await screen.findByText(
        "Brievie leest de map 'Oude scans' niet. Koppel hem aan een persoon of afdeling, of haal de bestanden eruit.",
      ),
    ).toBeVisible();
    await screen.findByRole("option", { name: "Lid" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Koppel aan …" }), "p-lid");
    await user.click(screen.getByRole("button", { name: "Koppelen" }));
    expect(state.posts).toEqual([{ person_id: "p-lid", name: "Oude scans" }]);
  });
});
