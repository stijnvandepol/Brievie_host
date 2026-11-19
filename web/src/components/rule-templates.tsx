import { t } from "@lingui/core/macro";
import { Link } from "@tanstack/react-router";
import { useId } from "react";
import type { components } from "../api/schema";
import { Icon } from "./icons";
import { Badge } from "./ui";

type RuleTemplate = components["schemas"]["RuleTemplateOut"];

/** Whom an example is meant for, in the words of the workspace kinds. */
export function audienceLabel(kinds: string[]): string {
  const home = kinds.includes("home");
  const business = kinds.includes("business");
  if (home && business) return t`Thuis en zakelijk`;
  return business ? t`Zakelijk` : t`Thuis`;
}

/** Wanneer, als, dan: what a rule is, in three lines. */
export function HowRulesWork() {
  const id = useId();
  return (
    <section className="panel panel-pad rule-explainer" aria-labelledby={id}>
      <h2 id={id}>{t`Zo werkt een regel`}</h2>
      <dl>
        <dt>{t`Wanneer`}</dt>
        <dd>{t`Bij nieuwe post en als je een brief bevestigt. Bestaande post alleen als je daarom vraagt.`}</dd>
        <dt>{t`Als`}</dt>
        <dd>{t`De voorwaarden: bijvoorbeeld een woord in de brief, of van wie de brief komt.`}</dd>
        <dt>{t`Dan`}</dt>
        <dd>{t`De acties: een label of een naam op de brief. Eerst als voorstel dat jij bevestigt, tenzij je de regel automatisch laat toepassen.`}</dd>
      </dl>
    </section>
  );
}

/** The examples, those for this workspace's kind first; each opens the editor filled in. */
export function TemplateGallery({
  workspaceId,
  kind,
  templates,
}: {
  workspaceId: string;
  kind: string;
  templates: RuleTemplate[];
}) {
  const id = useId();
  const sorted = [
    ...templates.filter((template) => template.kinds.includes(kind)),
    ...templates.filter((template) => !template.kinds.includes(kind)),
  ];
  return (
    <section className="template-gallery" aria-labelledby={id}>
      <h2 id={id}>{t`Begin met een voorbeeld`}</h2>
      <p className="muted">
        {t`Kies een voorbeeld en vul de gemarkeerde delen in. Er wordt niets opgeslagen tot je op Opslaan drukt.`}
      </p>
      <ul className="template-list">
        {sorted.map((template) => {
          const title = template.title;
          return (
            <li key={template.key} className="template-card">
              <div className="template-head">
                <h3>{title}</h3>
                <Badge>{audienceLabel(template.kinds)}</Badge>
              </div>
              <p>{template.description}</p>
              <Link
                to="/w/$workspaceId/automatisering/nieuw"
                params={{ workspaceId }}
                search={{ voorbeeld: template.key }}
                className="button secondary"
                aria-label={t`Gebruik dit voorbeeld: ${title}`}
              >
                <Icon name="add" />
                {t`Gebruik dit voorbeeld`}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
