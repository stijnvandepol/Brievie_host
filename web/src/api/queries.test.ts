import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { API, HttpResponse, http, server, workspace } from "../test-utils";
import { documentQuery, documentsQuery, inboxCountsQuery, READING_REFETCH_MS } from "./queries";

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

/** What a query's `refetchInterval` says for the data it holds now. */
async function intervalAfterFetch(
  options: ReturnType<typeof documentQuery> | ReturnType<typeof inboxCountsQuery>,
) {
  const client = new QueryClient();
  await client.fetchQuery(options as never);
  const query = client.getQueryCache().find({ queryKey: options.queryKey });
  const interval = options.refetchInterval;
  if (typeof interval !== "function" || !query) throw new Error("no interval function");
  return (interval as (query: unknown) => unknown)(query);
}

describe("polling while letters are being read", () => {
  const detail = `*${API}/workspaces/${workspace.id}/documents/d-1`;

  it("fetches a document that is processing again every 5 seconds, others not", async () => {
    server.use(http.get(detail, () => HttpResponse.json({ status: "processing" })));
    expect(await intervalAfterFetch(documentQuery(workspace.id, "d-1"))).toBe(READING_REFETCH_MS);
    expect(READING_REFETCH_MS).toBe(5000);
    server.use(http.get(detail, () => HttpResponse.json({ status: "done" })));
    expect(await intervalAfterFetch(documentQuery(workspace.id, "d-1"))).toBe(false);
  });

  it("fetches the inbox counts again while something is new, else not", async () => {
    const counts = `*${API}/workspaces/${workspace.id}/inbox/counts`;
    server.use(http.get(counts, () => HttpResponse.json({ new: 2, review: 0, unassigned: 0 })));
    expect(await intervalAfterFetch(inboxCountsQuery(workspace.id))).toBe(READING_REFETCH_MS);
    server.use(http.get(counts, () => HttpResponse.json({ new: 0, review: 1, unassigned: 0 })));
    expect(await intervalAfterFetch(inboxCountsQuery(workspace.id))).toBe(false);
  });

  it("polls the document list only when asked to", () => {
    expect(documentsQuery(workspace.id, {}, { poll: true }).refetchInterval).toBe(
      READING_REFETCH_MS,
    );
    expect(documentsQuery(workspace.id).refetchInterval).toBe(false);
  });
});
