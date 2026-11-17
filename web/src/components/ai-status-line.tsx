import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { aiStatusQuery } from "../api/queries";
import { Icon } from "./icons";

const day = new Intl.DateTimeFormat("nl-NL", { dateStyle: "long" });

/** The service as a member knows it: its name and country, or else only its address. */
function serviceName(supplier: string | null | undefined, host: string): string {
  switch (supplier) {
    case "mistral":
      return t`Mistral in Frankrijk (${host})`;
    case "openai":
      return t`OpenAI in de Verenigde Staten (${host})`;
    case "claude":
      return t`Claude van Anthropic in de Verenigde Staten (${host})`;
    default:
      return host;
  }
}

/**
 * One line that tells every member whether an AI reads the workspace's letters, where
 * their text goes and since when. Nothing while the answer is not known.
 */
export function AiStatusLine({ workspaceId }: { workspaceId: string }) {
  const { data } = useQuery(aiStatusQuery(workspaceId));
  if (!data) return null;
  let sentence: string;
  let warning = false;
  if (data.mode === "external" && data.host && data.since) {
    const service = serviceName(data.supplier, data.host);
    const since = day.format(new Date(data.since));
    sentence = t`AI aan: tekst van brieven gaat naar ${service}, ingesteld op ${since}.`;
  } else if (data.mode === "external") {
    warning = true;
    sentence = t`AI is ingesteld, maar niemand heeft daar akkoord voor gegeven. De AI leest daarom geen brieven. De beheerder kan AI opnieuw instellen.`;
  } else if (data.mode === "local") {
    sentence = t`AI aan op je eigen netwerk: de tekst van brieven gaat niet naar een dienst daarbuiten.`;
  } else {
    sentence = t`AI uit: de tekst van brieven gaat naar geen enkele AI-dienst.`;
  }
  return (
    <p className="ai-status muted small">
      <Icon name={warning ? "warning" : "info"} className="ai-status-icon" />
      <span>{sentence}</span>
    </p>
  );
}
