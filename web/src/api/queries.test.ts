import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { API, HttpResponse, http, server, workspace } from "../test-utils";
import { documentsQuery } from "./queries";

describe("documentsQuery", () => {
  it("sends only the filters that are set", async () => {
    const seen: string[] = [];
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents`, ({ request }) => {
        seen.push(new URL(request.url).search);
        return HttpResponse.json([]);
      }),
    );
    const client = new QueryClient();

    await client.fetchQuery(documentsQuery(workspace.id));
    await client.fetchQuery(
      documentsQuery(workspace.id, { inbox_state: "unassigned", person_id: "p-1", label_id: "" }),
    );

    expect(seen).toEqual(["", "?inbox_state=unassigned&person_id=p-1"]);
  });
});
