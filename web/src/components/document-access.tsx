import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiFailure, api } from "../api/client";
import { ErrorAlert } from "./page";
import { Loading } from "./ui";

/** Who can see one letter, by name, with the reasons the server gives ("op naam van",
 * "afdeling", "verdeelt post", "is gedeeld", ...). Asked only when opened. */
export function DocumentAccess({
  workspaceId,
  documentId,
}: {
  workspaceId: string;
  documentId: string;
}) {
  const [open, setOpen] = useState(false);
  const access = useQuery({
    queryKey: ["workspaces", workspaceId, "documents", documentId, "access"],
    enabled: open,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/access",
        { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  return (
    <details
      className="panel letter-access"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{t`Wie kan deze brief zien?`}</summary>
      {open && access.isPending ? <Loading /> : null}
      <ErrorAlert error={access.error} />
      {access.data ? (
        <ul className="sentence-list">
          {access.data.viewers.map((viewer) => (
            <li key={viewer.user_id}>
              <strong>{viewer.name}</strong>: {viewer.reasons.join(", ")}
            </li>
          ))}
        </ul>
      ) : null}
    </details>
  );
}
