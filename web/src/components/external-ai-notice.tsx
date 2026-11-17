import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { aiStatusQuery } from "../api/queries";

/** Says that the names on the page go to the AI service, when that service is external. */
export function ExternalAiNotice({ workspaceId }: { workspaceId: string }) {
  const { data } = useQuery(aiStatusQuery(workspaceId));
  if (data?.mode !== "external") return null;
  return (
    <p className="notice">
      {t`Je gebruikt een externe AI-dienst. De namen op deze pagina gaan mee met elke brief die de AI leest.`}
    </p>
  );
}
