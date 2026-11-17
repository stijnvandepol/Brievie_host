import { QueryClient, type QueryKey, type UseQueryOptions } from "@tanstack/react-query";
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

/** What a query's `refetchInterval` says for the data it holds after one fetch. */
async function intervalAfterFetch<TData, TKey extends QueryKey>(
  options: UseQueryOptions<TData, Error, TData, TKey> & { queryKey: TKey },
) {
  const client = new QueryClient();
  await client.fetchQuery(options);
  const query = client.getQueryCache().build(client, options);
  const interval = options.refetchInterval;
  return typeof interval === "function" ? interval(query) : interval;
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

  it("polls the document list only when asked to, and only while it holds letters", async () => {
    const list = `*${API}/workspaces/${workspace.id}/documents`;
    server.use(http.get(list, () => HttpResponse.json([{ id: "d-1" }])));
    expect(await intervalAfterFetch(documentsQuery(workspace.id, {}, { poll: true }))).toBe(
      READING_REFETCH_MS,
    );
    expect(await intervalAfterFetch(documentsQuery(workspace.id))).toBe(false);
    server.use(http.get(list, () => HttpResponse.json([])));
    expect(await intervalAfterFetch(documentsQuery(workspace.id, {}, { poll: true }))).toBe(false);
  });
});
