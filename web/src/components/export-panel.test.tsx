import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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

const path = `/w/${workspace.id}/leden`;
const base = `*${API}/workspaces/${workspace.id}`;

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
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Mijn post exporteren" }));
    expect(await screen.findByText("Wordt gemaakt…")).toBeInTheDocument();
    expect(asked).toBe(1);
    const link = await screen.findByRole("link", { name: /Download \(3 brieven, geldig tot/ });
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
    expect(await screen.findByRole("link", { name: /Download \(3 brieven/ })).toBeInTheDocument();
  });

  it("is hidden with a sentence when the workspace does not allow it", async () => {
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      ...memberHandlers(members, [], exportState("none", false)),
    );
    renderApp(path);
    expect(
      await screen.findByText("Exporteren staat uit voor deze werkruimte."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mijn post exporteren" })).not.toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Mijn post exporteren" })).toBeInTheDocument();
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
    await u.click(await screen.findByRole("button", { name: "Mijn post exporteren" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Je export wordt al gemaakt.");
  });

  it("the leave dialog offers the export first", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers());
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
      within(dialog).getByRole("button", { name: "Mijn post exporteren" }),
    ).toBeInTheDocument();
  });

  it("the leave dialog says nothing about export when it is not allowed", async () => {
    server.use(...loggedInHandlers(), ...memberHandlers(members, [], exportState("none", false)));
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Werkruimte verlaten?" });
    expect(within(dialog).queryByText(/Exporteer eerst/)).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Mijn post exporteren" }),
    ).not.toBeInTheDocument();
  });
});
