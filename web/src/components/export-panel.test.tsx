import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API,
  asRole,
  errorBody,
  exportState,
  HttpResponse,
  http,
  loggedInHandlers,
  memberHandlers,
  members,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/mensen`;
const base = `*${API}/workspaces/${workspace.id}`;

afterEach(() => vi.useRealTimers());

/** Everybody an admin: the only admin cannot leave, so the leave dialog needs a second. */
const twoAdmins = members.map((member) => ({ ...member, role: "admin" }));

describe("my export", () => {
  it("a member exports, waits and downloads", async () => {
    let reads = 0;
    let asked = 0;
    server.use(
      asRole("member"),
      http.post(`${base}/my-export`, () => {
        asked += 1;
        return HttpResponse.json(exportState("pending"), { status: 202 });
      }),
      http.get(`${base}/my-export`, () => {
        reads += 1;
        // The first read is the page's own; the next one is the poll.
        return HttpResponse.json(reads === 1 ? exportState("none") : exportState("ready"));
      }),
      ...loggedInHandlers(),
      http.get(`${base}/members`, () => HttpResponse.json(members)),
      http.get(`${base}/invitations`, () => HttpResponse.json([])),
    );
    // Only the poll is faked: the interval of the query, not the timers of the test tools.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Mijn archief exporteren" }));
    expect(await screen.findByText("Wordt gemaakt…")).toBeInTheDocument();
    expect(asked).toBe(1);
    expect(reads).toBe(1);
    await vi.advanceTimersByTimeAsync(3000);
    const link = await screen.findByRole("link", { name: /Download \(3 documenten, geldig tot/ });
    expect(link).toHaveAttribute("href", `/api/v1/workspaces/${workspace.id}/my-export/file`);
    expect(screen.queryByText("Wordt gemaakt…")).not.toBeInTheDocument();
  });

  it("shows a finished export straight away", async () => {
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      ...memberHandlers(members, [], exportState("ready")),
    );
    renderApp(path);
    expect(
      await screen.findByRole("link", { name: /Download \(3 documenten/ }),
    ).toBeInTheDocument();
  });

  it("is hidden with a sentence when the workspace does not allow it", async () => {
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      ...memberHandlers(twoAdmins, [], exportState("none", false)),
    );
    renderApp(path);
    expect(
      await screen.findByText(
        "Exporteren staat uit voor deze werkruimte. Vraag een beheerder om dit aan te zetten.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Mijn archief exporteren" }),
    ).not.toBeInTheDocument();
  });

  it("points an admin to Instellingen when export is off", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers(twoAdmins, [], exportState("none", false)));
    renderApp(path);
    const link = await screen.findByRole("link", { name: "Zet het aan bij Instellingen." });
    expect(link).toHaveAttribute("href", `/w/${workspace.id}/instellingen`);
    expect(
      screen.getByText(/Exporteren staat uit voor de leden van deze werkruimte\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Vraag een beheerder/)).not.toBeInTheDocument();
  });

  it("says when the export did not work and offers it again", async () => {
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      ...memberHandlers(members, [], exportState("failed")),
    );
    renderApp(path);
    expect(
      await screen.findByText("De export is niet gelukt. Probeer het opnieuw."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mijn archief exporteren" })).toBeInTheDocument();
  });

  it("shows the server's sentence when an export cannot start", async () => {
    server.use(
      http.post(`${base}/my-export`, () =>
        HttpResponse.json(errorBody("export_running", "Je export wordt al gemaakt."), {
          status: 409,
        }),
      ),
      ...loggedInHandlers(),
      ...memberHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Mijn archief exporteren" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Je export wordt al gemaakt.");
  });

  it("the leave dialog offers the export first", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers(twoAdmins));
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Werkruimte verlaten?" });
    expect(
      within(dialog).getByText(
        "Exporteer eerst je post: na het verlaten kun je niet meer downloaden.",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Mijn archief exporteren" }),
    ).toBeInTheDocument();
  });

  it("the leave dialog says nothing about export when it is not allowed", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers(twoAdmins, [], exportState("none", false)));
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Werkruimte verlaten?" });
    expect(within(dialog).queryByText(/Exporteer eerst/)).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Mijn archief exporteren" }),
    ).not.toBeInTheDocument();
  });

  it("a member never sees the admin's scope", async () => {
    server.use(asRole("member"), ...loggedInHandlers(), ...memberHandlers());
    renderApp(path);
    expect(await screen.findByRole("button", { name: "Mijn archief exporteren" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Alles exporteren" })).not.toBeInTheDocument();
  });

  it("an admin exports everything they may read, with the explanation", async () => {
    const scopes: (string | null)[] = [];
    server.use(
      http.post(`${base}/my-export`, ({ request }) => {
        scopes.push(new URL(request.url).searchParams.get("scope"));
        return HttpResponse.json(exportState("pending", true, "all"), { status: 202 });
      }),
      ...loggedInHandlers(),
      ...memberHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Alles exporteren" }));
    expect(
      screen.getByText(
        "Alles exporteren: alles wat jij mag lezen. Privébrieven die jij niet mag lezen, zitten er niet in. Het is geen back-up.",
      ),
    ).toBeVisible();
    await screen.findByText("Wordt gemaakt…");
    expect(scopes).toEqual(["all"]);
  });

  it("a member starts the export of their own archive", async () => {
    const scopes: (string | null)[] = [];
    server.use(
      asRole("member"),
      http.post(`${base}/my-export`, ({ request }) => {
        scopes.push(new URL(request.url).searchParams.get("scope"));
        return HttpResponse.json(exportState("pending"), { status: 202 });
      }),
      ...loggedInHandlers(),
      ...memberHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Mijn archief exporteren" }));
    await screen.findByText("Wordt gemaakt…");
    expect(scopes).toEqual(["mine"]);
  });

  it("an admin can export everything while member exports are off", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers(twoAdmins, [], exportState("none", false)));
    renderApp(path);
    expect(await screen.findByRole("button", { name: "Alles exporteren" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Mijn archief exporteren" }),
    ).not.toBeInTheDocument();
  });

  it("a finished export of everything offers one download and says it expires", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(members, [], exportState("ready", true, "all")),
    );
    renderApp(path);
    const links = await screen.findAllByRole("link", {
      name: /Download alles \(3 documenten, geldig tot/,
    });
    expect(links).toHaveLength(1);
  });
});
