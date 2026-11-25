import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import {
  API,
  asRole,
  documentHandlers,
  documentSummary,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const uploadUrl = `*${API}/workspaces/${workspace.id}/documents/files`;
const archive = `/w/${workspace.id}/archief`;

function pdf(name: string) {
  return new File(["%PDF-1.4 brief"], name, { type: "application/pdf" });
}

async function choose(files: File[]) {
  const input = (await screen.findByTestId("letter-files")) as HTMLInputElement;
  await userEvent.upload(input, files);
}

/** The metadata of every upload, in the order sent. jsdom cannot read a multipart body back
 * (its File is not a stream undici reads), so the client call is watched instead. */
function watchUploads(): Record<string, unknown>[] {
  const sent: Record<string, unknown>[] = [];
  const original = api.POST.bind(api);
  vi.spyOn(api, "POST").mockImplementation(((
    path: string,
    init: { body?: { metadata?: string } },
  ) => {
    if (path.endsWith("/documents/files")) sent.push(JSON.parse(init.body?.metadata ?? "{}"));
    return original(path as never, init as never);
  }) as typeof api.POST);
  return sent;
}

afterEach(() => vi.restoreAllMocks());

describe("Brief toevoegen", () => {
  it("is not there for a read-only member", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...documentHandlers());
    renderApp(archive);
    await screen.findByRole("heading", { name: "Archief" });
    expect(screen.queryByRole("button", { name: "Brief toevoegen" })).not.toBeInTheDocument();
  });

  it("sends one request per file and says each was added", async () => {
    const sent = watchUploads();
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(uploadUrl, () =>
        HttpResponse.json({ ...documentSummary, status: "processing" }, { status: 201 }),
      ),
    );
    renderApp(archive);
    expect(await screen.findByRole("button", { name: "Brief toevoegen" })).toBeInTheDocument();
    await choose([pdf("een.pdf"), pdf("twee.pdf")]);
    await waitFor(() =>
      expect(screen.getAllByText("Toegevoegd. Brievie leest de brief nu.")).toHaveLength(2),
    );
    expect(sent).toHaveLength(2);
    expect(sent[0]?.client_id).not.toBe(sent[1]?.client_id);
  });

  it("refuses more than 50 files without sending any", async () => {
    let sent = 0;
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(uploadUrl, () => {
        sent += 1;
        return HttpResponse.json(documentSummary, { status: 201 });
      }),
    );
    renderApp(archive);
    await choose(Array.from({ length: 51 }, (_, index) => pdf(`brief-${index}.pdf`)));
    expect(await screen.findByText("Kies maximaal 50 bestanden tegelijk.")).toBeInTheDocument();
    expect(sent).toBe(0);
  });

  it("shows the server's sentence on the failed file only, and a retry keeps its id", async () => {
    const sent = watchUploads();
    let calls = 0;
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(uploadUrl, () => {
        calls += 1;
        // The first file is sent first.
        if (calls === 1) {
          return HttpResponse.json(
            errorBody("file_encrypted", "Deze PDF is beveiligd met een wachtwoord."),
            { status: 422 },
          );
        }
        return HttpResponse.json(documentSummary, { status: 201 });
      }),
    );
    renderApp(archive);
    await choose([pdf("beveiligd.pdf"), pdf("goed.pdf")]);
    expect(
      await screen.findByText("Deze PDF is beveiligd met een wachtwoord."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Toegevoegd. Brievie leest de brief nu.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Opnieuw proberen" }));
    await waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2]?.client_id).toBe(sent[0]?.client_id);
    expect(await screen.findAllByText("Toegevoegd. Brievie leest de brief nu.")).toHaveLength(2);
  });

  it("has at most three requests in flight", async () => {
    let inFlight = 0;
    let most = 0;
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(uploadUrl, async () => {
        inFlight += 1;
        most = Math.max(most, inFlight);
        await delay(30);
        inFlight -= 1;
        return HttpResponse.json(documentSummary, { status: 201 });
      }),
    );
    renderApp(archive);
    await choose([1, 2, 3, 4, 5].map((n) => pdf(`brief-${n}.pdf`)));
    await waitFor(() =>
      expect(screen.getAllByText("Toegevoegd. Brievie leest de brief nu.")).toHaveLength(5),
    );
    expect(most).toBe(3);
  });

  it("adds an own document with its title and type", async () => {
    const sent = watchUploads();
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers(),
      http.post(uploadUrl, () => HttpResponse.json(documentSummary, { status: 201 })),
    );
    renderApp(archive);
    await userEvent.click(await screen.findByRole("button", { name: "Document toevoegen" }));
    const form = within(screen.getByRole("form", { name: "Document toevoegen" }));
    await userEvent.type(form.getByLabelText("Titel"), "Polis inboedel");
    await userEvent.selectOptions(form.getByLabelText("Type"), "policy");
    await userEvent.upload(form.getByLabelText("Bestand"), pdf("polis.pdf"));
    await userEvent.click(form.getByRole("button", { name: "Toevoegen" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(
      await screen.findByText("Toegevoegd. Brievie leest het document nu."),
    ).toBeInTheDocument();
    expect(sent[0]).toMatchObject({
      kind: "document",
      title: "Polis inboedel",
      document_type: "policy",
    });
  });
});
