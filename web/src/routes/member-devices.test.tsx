import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  asRole,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  memberHandlers,
  members,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const bram = members[1] as (typeof members)[number];
const path = `/w/${workspace.id}/leden/${bram.user_id}`;
const phone = {
  id: "5d1c3b7e-2f6a-4c3e-9b1a-7a2d4e5f7001",
  name: "iPhone van Bram",
  created_at: "2026-11-14T12:41:00Z",
  last_seen_at: null,
  app_version: "1.0.0",
};

function userHandlers(list = [phone]) {
  return [
    ...loggedInHandlers(),
    ...memberHandlers(),
    http.get(`*${API}/users/${bram.user_id}/devices`, () => HttpResponse.json(list)),
  ];
}

describe("a member's devices and sessions", () => {
  it("shows the devices of the member to an admin", async () => {
    server.use(...userHandlers());
    renderApp(path);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Apparaten en sessies van Bram" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("rowheader", { name: "iPhone van Bram" })).toBeInTheDocument();
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Kruimelpad" })).getByRole("link", {
        name: "Leden",
      }),
    ).toBeInTheDocument();
  });

  it("an admin revokes a member's device after confirming", async () => {
    let list = [phone];
    let revoked = "";
    server.use(
      http.get(`*${API}/users/${bram.user_id}/devices`, () => HttpResponse.json(list)),
      http.delete(`*${API}/users/${bram.user_id}/devices/:id`, ({ request }) => {
        revoked = new URL(request.url).pathname.split("/").at(-1) ?? "";
        list = [];
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      ...memberHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Intrekken: iPhone van Bram" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Apparaat intrekken?" });
    expect(dialog).toHaveTextContent("Bram kan het apparaat opnieuw koppelen.");
    expect(revoked).toBe("");
    await u.click(within(dialog).getByRole("button", { name: "Intrekken" }));
    await waitFor(() =>
      expect(screen.queryByRole("rowheader", { name: "iPhone van Bram" })).not.toBeInTheDocument(),
    );
    expect(revoked).toBe(phone.id);
  });

  it("an admin ends a member's sessions after confirming", async () => {
    let ended = false;
    server.use(
      http.delete(`*${API}/users/${bram.user_id}/sessions`, () => {
        ended = true;
        return new HttpResponse(null, { status: 204 });
      }),
      ...userHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Alle sessies beëindigen" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Alle sessies beëindigen?" });
    expect(dialog).toHaveTextContent("Bram wordt overal uitgelogd en krijgt daar een melding van.");
    expect(ended).toBe(false);
    await u.click(within(dialog).getByRole("button", { name: "Beëindigen" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Bram is overal uitgelogd.");
    expect(ended).toBe(true);
  });

  it("shows the server's sentence when a server admin may not be touched", async () => {
    server.use(
      http.delete(`*${API}/users/${bram.user_id}/sessions`, () =>
        HttpResponse.json(
          errorBody("server_admin_only", "Alleen een serverbeheerder mag dit bij dit account."),
          { status: 403 },
        ),
      ),
      ...userHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Alle sessies beëindigen" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("button", { name: "Beëindigen" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Alleen een serverbeheerder mag dit bij dit account.",
    );
  });

  it("a member is sent back to the list and gets no controls", async () => {
    server.use(asRole("member"), ...userHandlers());
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Leden" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Alle sessies beëindigen" }),
    ).not.toBeInTheDocument();
  });
});
