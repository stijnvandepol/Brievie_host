import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { API, HttpResponse, http, loggedInHandlers, renderApp, server, user } from "../test-utils";

const serverAdmin = { ...user, is_server_admin: true };

const tunnelOn = {
  hostname: "192.168.1.20",
  vpn_hostname: "",
  public_hostname: "brievie.example.nl",
  addresses: [
    { url: "https://192.168.1.20:443", kind: "home", certificate: "private_ca" },
    { url: "https://brievie.example.nl", kind: "public", certificate: "public" },
  ],
  public_web: false,
  tunnel: { token_set: true, connected: "yes" },
  trusted_proxies: [],
  second_factor_available: false,
  seen_as: { route: "private", secure: true, client_ip: "192.168.1.30", peer_ip: "172.18.0.1" },
  checks: [
    { code: "public_web_off", level: "ok" },
    { code: "cloudflare_reads_along", level: "warning" },
    { code: "app_update_needed", level: "warning" },
  ],
  docs_url: "https://brievie.com/docs/installeren#van-buiten",
};

function asServerAdmin(found: object) {
  server.use(
    http.get(`*${API}/me`, () => HttpResponse.json(serverAdmin)),
    ...loggedInHandlers(),
    http.get(`*${API}/server/external-access`, () => HttpResponse.json(found)),
  );
}

describe("the external access page", () => {
  it("shows one panel per way with on/off", async () => {
    asServerAdmin(tunnelOn);
    renderApp("/server/van-buiten");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Van buiten bereikbaar" }),
    ).toBeInTheDocument();
    const home = await screen.findByRole("region", { name: /Thuisnetwerk/ });
    expect(within(home).getByText("Aan")).toBeInTheDocument();
    expect(within(home).getByText("https://192.168.1.20:443")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: /VPN/ })).getByText("Uit"),
    ).toBeInTheDocument();
    const tunnel = screen.getByRole("region", { name: /Cloudflare Tunnel/ });
    expect(within(tunnel).getByText("Aan")).toBeInTheDocument();
    expect(within(tunnel).getByText("https://brievie.example.nl")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: /Eigen reverse proxy/ })).getByText("Uit"),
    ).toBeInTheDocument();
  });

  it("shows each check with text and icon, not colour alone", async () => {
    asServerAdmin(tunnelOn);
    renderApp("/server/van-buiten");
    const list = await screen.findByRole("list", { name: "Controles" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("In orde");
    expect(items[0]).toHaveTextContent("Via het publieke adres werkt alleen de app.");
    expect(items[1]).toHaveTextContent("Let op");
    expect(items[1]).toHaveTextContent("Cloudflare kan alles lezen wat er langskomt");
    for (const item of items) {
      expect(item.querySelector("svg")).not.toBeNull();
    }
  });

  it("shows the address Brievie sees", async () => {
    asServerAdmin(tunnelOn);
    renderApp("/server/van-buiten");
    const seen = await screen.findByRole("region", { name: "Zo ziet Brievie jouw verbinding" });
    expect(within(seen).getByText("192.168.1.30")).toBeInTheDocument();
    expect(within(seen).getByText("172.18.0.1")).toBeInTheDocument();
    expect(within(seen).getByText("Thuisnetwerk of VPN")).toBeInTheDocument();
  });

  it("links to the website docs", async () => {
    asServerAdmin(tunnelOn);
    renderApp("/server/van-buiten");
    const link = await screen.findByRole("link", { name: "Uitleg en voorbeelden op de website" });
    expect(link).toHaveAttribute("href", "https://brievie.com/docs/installeren#van-buiten");
  });

  it("has no input fields and shows no token", async () => {
    asServerAdmin(tunnelOn);
    renderApp("/server/van-buiten");
    await screen.findByRole("list", { name: "Controles" });
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    expect(screen.getByText(/het token zelf staat nooit op dit scherm/)).toBeInTheDocument();
  });

  it("renders the red line for public web without a second factor", async () => {
    asServerAdmin({
      ...tunnelOn,
      public_web: true,
      checks: [{ code: "public_web_without_second_factor", level: "problem" }],
    });
    renderApp("/server/van-buiten");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Inloggen op de webinterface kan van buiten zonder tweede stap.",
    );
    expect(screen.getByText("Probleem")).toBeInTheDocument();
  });

  it("is hidden from members who are not server admins", async () => {
    server.use(...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(
      within(menu).queryByRole("link", { name: "Van buiten bereikbaar" }),
    ).not.toBeInTheDocument();
  });

  it("is in a server admin's menu", async () => {
    asServerAdmin(tunnelOn);
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).getByRole("link", { name: "Van buiten bereikbaar" })).toBeInTheDocument();
  });
});

describe("what the server saw on the way in", () => {
  it("names the local address that asked for the public name", async () => {
    asServerAdmin({
      ...tunnelOn,
      tunnel: { token_set: false, connected: "not_set" },
      trusted_proxies: [],
      untrusted_public_name_peer: "192.168.1.5",
      checks: [{ code: "public_name_from_untrusted_source", level: "problem" }],
    });
    renderApp("/server/van-buiten");
    const proxy = await screen.findByRole("region", { name: /Eigen reverse proxy/ });
    expect(within(proxy).getByText("192.168.1.5")).toBeInTheDocument();
    expect(proxy).toHaveTextContent("alleen in BRIEVIE_TRUSTED_PROXIES als dit je eigen proxy is");
    const list = screen.getByRole("list", { name: "Controles" });
    expect(list).toHaveTextContent("Er kwamen verzoeken voor de publieke naam binnen");
  });
});
