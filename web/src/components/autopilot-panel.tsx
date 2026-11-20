import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Switch } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { aiAutopilotQuery, aiStatusQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmDialog } from "./confirm-dialog";
import { ErrorAlert } from "./page";

type Autopilot = components["schemas"]["AiAutopilotOut"];
type Which = "auto_organise" | "auto_confirm";

/** The server's notice as paragraphs: a blank line starts a new one, single breaks stay. */
function Notice({ text }: { text: string }) {
  return (
    <>
      {text
        .split(/\n\s*\n/)
        .filter((part) => part.trim() !== "")
        .map((part, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: fixed text, two paragraphs may be equal
          <p key={index} className="consent-notice">
            {part.trim()}
          </p>
        ))}
    </>
  );
}

/** One switch with its label and a one-sentence description. */
function AutopilotSwitch({
  label,
  description,
  isSelected,
  isDisabled,
  onChange,
}: {
  label: string;
  description: string;
  isSelected: boolean;
  isDisabled: boolean;
  onChange: (on: boolean) => void;
}) {
  const hintId = useId();
  return (
    <div>
      <Switch
        className="switch"
        isSelected={isSelected}
        isDisabled={isDisabled}
        onChange={onChange}
        aria-describedby={hintId}
      >
        <span className="switch-track" aria-hidden="true" />
        {label}
      </Switch>
      <p id={hintId} className="switch-hint">
        {description}
      </p>
    </div>
  );
}

/**
 * The two switches. While the server forbids the autopilot only a switch still stored on is
 * shown, so it can be switched off; switching on is not offered then.
 */
function Switches({ workspaceId, autopilot }: { workspaceId: string; autopilot: Autopilot }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState<Which | null>(null);
  const save = useMutation({
    mutationFn: async (change: { which: Which; on: boolean; acknowledged: boolean }) => {
      const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/ai/autopilot", {
        params: { path: { workspace_id: workspaceId } },
        body: {
          auto_organise: autopilot.auto_organise,
          auto_confirm: autopilot.auto_confirm,
          [change.which]: change.on,
          acknowledged: change.acknowledged,
        },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(aiAutopilotQuery(workspaceId).queryKey, data);
    },
  });

  function change(which: Which, on: boolean) {
    // Switching on needs the notice and a yes; switching off saves at once.
    if (on && !autopilot.allowed) return;
    if (on) setAsking(which);
    else save.mutate({ which, on, acknowledged: false });
  }

  const shown = (on: boolean) => autopilot.allowed || on;
  return (
    <>
      {shown(autopilot.auto_organise) ? (
        <AutopilotSwitch
          label={t`Brieven zelf onder personen en labels zetten`}
          description={t`Brievie zet een brief zelf bij de juiste persoon en het juiste label als de AI of de naam duidelijk is.`}
          isSelected={autopilot.auto_organise}
          isDisabled={save.isPending}
          onChange={(on) => change("auto_organise", on)}
        />
      ) : null}
      {shown(autopilot.auto_confirm) ? (
        <AutopilotSwitch
          label={t`Brieven zelf bevestigen`}
          description={t`Brievie bevestigt een brief zelf als alles in de brief duidelijk te lezen is. Anders blijft hij staan om te controleren.`}
          isSelected={autopilot.auto_confirm}
          isDisabled={save.isPending}
          onChange={(on) => change("auto_confirm", on)}
        />
      ) : null}
      <ErrorAlert error={asking === null ? save.error : null} />
      <ConfirmDialog
        isOpen={asking !== null}
        onOpenChange={(open) => {
          if (!open) {
            setAsking(null);
            // A refusal belongs to the question; cancelled, it is not shown again.
            save.reset();
          }
        }}
        title={t`Automatisch verwerken aanzetten?`}
        confirmLabel={t`Aanzetten`}
        tone="primary"
        onConfirm={async () => {
          if (asking === null) return;
          await save.mutateAsync({ which: asking, on: true, acknowledged: true });
        }}
      >
        <Notice text={autopilot.notice} />
      </ConfirmDialog>
    </>
  );
}

/** A line for a member: what Brievie does with letters on its own, switch by switch. */
function MemberLine({ autopilot }: { autopilot: Autopilot }) {
  const organise = autopilot.allowed && autopilot.auto_organise;
  const confirm = autopilot.allowed && autopilot.auto_confirm;
  const lines = [
    organise ? t`Brievie zet brieven zelf onder personen en labels.` : null,
    confirm ? t`Brievie bevestigt brieven zelf.` : null,
  ].filter((line): line is string => line !== null);
  return (
    <p className="muted">
      {lines.length > 0 ? lines.join(" ") : t`Brieven worden niet automatisch verwerkt.`}
    </p>
  );
}

/**
 * "Automatisch verwerken" on the AI page. An administrator sees the two switches while AI is
 * on and the server allows them (while it forbids them, only a switch still on, to switch it
 * off); a member sees one line and nothing to switch.
 */
export function AutopilotPanel({ workspaceId, admin }: { workspaceId: string; admin: boolean }) {
  const status = useQuery({ ...aiStatusQuery(workspaceId), enabled: admin });
  const autopilot = useQuery(aiAutopilotQuery(workspaceId));
  const data = autopilot.data;
  if (!data) return <ErrorAlert error={autopilot.error} />;
  if (!admin) return <MemberLine autopilot={data} />;
  if (!status.data || status.data.mode === "off") return null;
  return (
    <section className="panel panel-pad" aria-labelledby="autopilot-heading">
      <h2 id="autopilot-heading">{t`Automatisch verwerken`}</h2>
      {data.allowed ? null : (
        <p className="muted">{t`De beheerder van de server heeft automatisch verwerken uitgezet. Hier kun je het niet aanzetten.`}</p>
      )}
      {data.allowed || data.auto_organise || data.auto_confirm ? (
        <Switches workspaceId={workspaceId} autopilot={data} />
      ) : null}
    </section>
  );
}
