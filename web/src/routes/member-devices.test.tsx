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
        name: "Mensen",
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
    expect(await screen.findByRole("heading", { level: 1, name: "Mensen" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Alle sessies beëindigen" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Tweestapsverificatie wissen" }),
    ).not.toBeInTheDocument();
  });

  it("an admin clears a lost second factor, unpairing the devices by default", async () => {
    let sent: unknown;
    server.use(
      http.post(`*${API}/users/${bram.user_id}/second-factor/clear`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ devices_revoked: 1 });
      }),
      ...userHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Tweestapsverificatie wissen" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Tweestapsverificatie van Bram wissen?",
    });
    const unpair = within(dialog).getByRole("checkbox", {
      name: "Ontkoppel ook de apparaten van Bram",
    });
    expect(unpair).toBeChecked();
    await u.type(within(dialog).getByLabelText("Jouw wachtwoord"), "mijn wachtwoord");
    await u.click(within(dialog).getByRole("button", { name: "Wissen" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "De tweestapsverificatie van Bram is gewist. 1 apparaten zijn ontkoppeld.",
    );
    expect(sent).toEqual({ password: "mijn wachtwoord", revoke_devices: true });
  });

  it("an admin may keep the member's devices", async () => {
    let sent: unknown;
    server.use(
      http.post(`*${API}/users/${bram.user_id}/second-factor/clear`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ devices_revoked: 0 });
      }),
      ...userHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Tweestapsverificatie wissen" }));
    const dialog = await screen.findByRole("alertdialog");
    await u.click(within(dialog).getByRole("checkbox"));
    await u.type(within(dialog).getByLabelText("Jouw wachtwoord"), "mijn wachtwoord");
    await u.click(within(dialog).getByRole("button", { name: "Wissen" }));

    await screen.findByRole("status");
    expect(sent).toEqual({ password: "mijn wachtwoord", revoke_devices: false });
  });
});
