import { describe, expect, it } from "vitest";
import { API, HttpResponse, http, server } from "../test-utils";
import { api, errorMessage } from "./client";

describe("api client", () => {
  it("sends the csrftoken cookie as X-CSRFToken on writes only", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const seen: Record<string, string | null> = {};
    server.use(
      http.get(`*${API}/me`, ({ request }) => {
        seen.get = request.headers.get("x-csrftoken");
        return HttpResponse.json({});
      }),
      http.post(`*${API}/auth/logout`, ({ request }) => {
        seen.post = request.headers.get("x-csrftoken");
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await api.GET("/api/v1/me");
    await api.POST("/api/v1/auth/logout");
    expect(seen).toEqual({ get: null, post: "abc" });
  });

  it("prefers the server message and falls back to a Dutch sentence", () => {
    expect(errorMessage({ code: "x", message: "Zin van de server." })).toBe("Zin van de server.");
    expect(errorMessage("<html>502</html>")).toMatch(/misgegaan/i);
    expect(errorMessage(new TypeError("Failed to fetch"))).toMatch(/niet bereiken/i);
  });
});
