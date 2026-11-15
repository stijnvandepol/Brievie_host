import { screen, waitFor, within } from "@testing-library/react";
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
} from "../test-utils";

const phone = {
  id: "5d1c3b7e-2f6a-4c3e-9b1a-7a2d4e5f6001",
  name: "iPhone van Anna",
  created_at: "2026-11-14T12:41:00Z",
  last_seen_at: null,
};
const tablet = { ...phone, id: "5d1c3b7e-2f6a-4c3e-9b1a-7a2d4e5f6002", name: "iPad" };

describe("device table", () => {
  it("lists a device with its date and an Actief label", async () => {
    server.use(
      http.get(`*${API}/devices`, () => HttpResponse.json([phone])),
      ...loggedInHandlers(),
    );
    renderApp("/koppel-app");

    const row = (await screen.findByRole("rowheader", { name: "iPhone van Anna" })).closest("tr");
    const scope = within(row as HTMLElement);
    expect(scope.getByText(/2026/)).toBeInTheDocument();
    expect(scope.getByText("Actief")).toBeInTheDocument();
    expect(scope.getByRole("button", { name: /intrekken/i })).toBeInTheDocument();
    for (const name of ["Apparaat", "Gekoppeld op", "Status"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("1 apparaat")).toBeInTheDocument();
  });

  it("asks for confirmation before revoking, calls the API and refetches", async () => {
    let devices = [phone, tablet];
    let revoked: string | undefined;
    let csrf: string | null = null;
    document.cookie = "csrftoken=tok123; path=/";
    server.use(
      http.get(`*${API}/devices`, () => HttpResponse.json(devices)),
      ...loggedInHandlers(),
      http.delete(`*${API}/devices/:id`, ({ params, request }) => {
        revoked = String(params.id);
        csrf = request.headers.get("x-csrftoken");
        devices = devices.filter((device) => device.id !== params.id);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");

    await u.click(await screen.findByRole("button", { name: "Intrekken: iPhone van Anna" }));
    const dialog = await screen.findByRole("alertdialog", { name: /apparaat intrekken/i });
    expect(revoked).toBeUndefined();

    // Cancelling does nothing.
    await u.click(within(dialog).getByRole("button", { name: "Annuleren" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(revoked).toBeUndefined();

    await u.click(screen.getByRole("button", { name: "Intrekken: iPhone van Anna" }));
    const again = await screen.findByRole("alertdialog");
    await u.click(within(again).getByRole("button", { name: "Intrekken" }));

    await waitFor(() => expect(revoked).toBe(phone.id));
    expect(csrf).toBe("tok123");
    await waitFor(() =>
      expect(screen.queryByRole("rowheader", { name: "iPhone van Anna" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("rowheader", { name: "iPad" })).toBeInTheDocument();
  });

  it("shows the server message when revoking fails", async () => {
    server.use(
      http.get(`*${API}/devices`, () => HttpResponse.json([phone])),
      http.delete(`*${API}/devices/:id`, () =>
        HttpResponse.json(errorBody("device_not_found", "We kunnen dit apparaat niet vinden."), {
          status: 404,
        }),
      ),
      ...loggedInHandlers(),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");

    await u.click(await screen.findByRole("button", { name: /intrekken/i }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Intrekken" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We kunnen dit apparaat niet vinden.",
    );
  });

  it("shows one calm sentence when no device is paired", async () => {
    server.use(...loggedInHandlers());
    renderApp("/koppel-app");

    expect(await screen.findByText("Er zijn nog geen apparaten gekoppeld.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
