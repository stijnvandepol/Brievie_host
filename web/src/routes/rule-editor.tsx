import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Button, Form, Input, Label, Switch, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { labelsQuery, personsQuery, rulesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { DocumentTable, typeLabel } from "../components/document-table";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import {
  DOCUMENT_TYPES,
  FIELD_OPERATORS,
  FIELDS,
  fieldLabel,
  operatorLabel,
  SOURCES,
  sourceLabel,
} from "../components/rule-sentence";
import { workspaceRoute } from "./workspace";

type RuleOut = components["schemas"]["RuleOut"];
type RuleBody = components["schemas"]["RuleIn"];
type LabelOut = components["schemas"]["LabelOut"];
type PersonOut = components["schemas"]["PersonOut"];

type ConditionRow = { key: number; field: string; op: string; value: string };
type ActionRow = { key: number; type: string; id: string };

type Choices = { labels: LabelOut[]; persons: PersonOut[] };

/** The value a condition starts with after its field changed. */
function defaultValue(field: string, op: string, { labels, persons }: Choices): string {
  if (op === "is_empty") return "";
  switch (field) {
    case "document_type":
      return DOCUMENT_TYPES[0] ?? "";
    case "label":
      return labels[0]?.id ?? "";
    case "addressee":
      return persons[0]?.id ?? "";
    case "source":
      return SOURCES[0] ?? "";
    default:
      return "";
  }
}

function defaultAction(type: string, { labels, persons }: Choices): string {
  return type === "add_label" ? (labels[0]?.id ?? "") : (persons[0]?.id ?? "");
}

let nextKey = 0;
const newKey = () => {
  nextKey += 1;
  return nextKey;
};

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; text: string }[];
}) {
  return (
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.text}
        </option>
      ))}
    </select>
  );
}

function ConditionEditor({
  row,
  number,
  choices,
  canRemove,
  onChange,
  onRemove,
}: {
  row: ConditionRow;
  number: number;
  choices: Choices;
  canRemove: boolean;
  onChange: (row: ConditionRow) => void;
  onRemove: () => void;
}) {
  const valueOptions: Record<string, { value: string; text: string }[]> = {
    document_type: DOCUMENT_TYPES.map((type) => ({ value: type, text: typeLabel(type) })),
    label: choices.labels.map((label) => ({ value: label.id, text: label.name })),
    addressee: choices.persons.map((person) => ({ value: person.id, text: person.display_name })),
    source: SOURCES.map((source) => ({ value: source, text: sourceLabel(source) })),
  };
  const options = valueOptions[row.field];
  return (
    <fieldset className="rule-row">
      <legend className="visually-hidden">{t`Voorwaarde ${number}`}</legend>
      <SelectField
        label={t`Veld`}
        value={row.field}
        options={FIELDS.map((field) => ({ value: field, text: fieldLabel(field) }))}
        onChange={(field) => {
          const op = FIELD_OPERATORS[field]?.[0] ?? "";
          onChange({ ...row, field, op, value: defaultValue(field, op, choices) });
        }}
      />
      <SelectField
        label={t`Vergelijking`}
        value={row.op}
        options={(FIELD_OPERATORS[row.field] ?? []).map((op) => ({
          value: op,
          text: operatorLabel(op),
        }))}
        onChange={(op) => onChange({ ...row, op, value: defaultValue(row.field, op, choices) })}
      />
      {row.op === "is_empty" ? null : options ? (
        <SelectField
          label={t`Waarde`}
          value={row.value}
          options={options}
          onChange={(value) => onChange({ ...row, value })}
        />
      ) : (
        <TextField
          aria-label={t`Waarde`}
          className="rule-value"
          value={row.value}
          onChange={(value) => onChange({ ...row, value })}
        >
          <Input />
        </TextField>
      )}
      <Button
        className="button-text danger"
        aria-label={t`Voorwaarde ${number} verwijderen`}
        isDisabled={!canRemove}
        onPress={onRemove}
      >
        {t`Verwijderen`}
      </Button>
    </fieldset>
  );
}

function ActionEditor({
  row,
  number,
  choices,
  canRemove,
  onChange,
  onRemove,
}: {
  row: ActionRow;
  number: number;
  choices: Choices;
  canRemove: boolean;
  onChange: (row: ActionRow) => void;
  onRemove: () => void;
}) {
  const isLabel = row.type === "add_label";
  return (
    <fieldset className="rule-row">
      <legend className="visually-hidden">{t`Actie ${number}`}</legend>
      <SelectField
        label={t`Actie`}
        value={row.type}
        options={[
          { value: "add_label", text: t`Label toevoegen` },
          { value: "add_addressee", text: t`Op naam zetten van` },
        ]}
        onChange={(type) => onChange({ ...row, type, id: defaultAction(type, choices) })}
      />
      <SelectField
        label={isLabel ? t`Label` : t`Persoon`}
        value={row.id}
        options={
          isLabel
            ? choices.labels.map((label) => ({ value: label.id, text: label.name }))
            : choices.persons.map((person) => ({ value: person.id, text: person.display_name }))
        }
        onChange={(id) => onChange({ ...row, id })}
      />
      <Button
        className="button-text danger"
        aria-label={t`Actie ${number} verwijderen`}
        isDisabled={!canRemove}
        onPress={onRemove}
      >
        {t`Verwijderen`}
      </Button>
    </fieldset>
  );
}

function RuleForm({
  workspaceId,
  rule,
  choices,
}: {
  workspaceId: string;
  rule: RuleOut | null;
  choices: Choices;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(rule?.name ?? "");
  const [match, setMatch] = useState(rule?.match ?? "all");
  const [mode, setMode] = useState(rule?.mode ?? "propose");
  const [active, setActive] = useState(rule?.active ?? true);
  const [stop, setStop] = useState(rule?.stop ?? false);
  const [conditions, setConditions] = useState<ConditionRow[]>(
    rule
      ? rule.conditions.map((condition) => ({ key: newKey(), ...condition }))
      : [{ key: newKey(), field: "text", op: "contains_word", value: "" }],
  );
  const [actions, setActions] = useState<ActionRow[]>(
    rule
      ? rule.actions.map((action) => ({
          key: newKey(),
          type: action.type,
          id: (action.type === "add_label" ? action.label_id : action.person_id) ?? "",
        }))
      : [{ key: newKey(), type: "add_label", id: defaultAction("add_label", choices) }],
  );
  // A rule that was just created is changed, not created again, by the next save.
  const [savedId, setSavedId] = useState<string | null>(rule?.id ?? null);
  const [savedActive, setSavedActive] = useState<boolean | null>(null);
  const [running, setRunning] = useState(false);

  function body(): RuleBody {
    return {
      name: name.trim(),
      active,
      mode,
      stop,
      match,
      conditions: conditions.map(({ field, op, value }) => ({ field, op, value })),
      actions: actions.map((action) =>
        action.type === "add_label"
          ? { type: action.type, label_id: action.id }
          : { type: action.type, person_id: action.id },
      ),
    };
  }

  const save = useMutation({
    mutationFn: async () => {
      const payload = body();
      const path = { workspace_id: workspaceId };
      const { data, error } = savedId
        ? await api.PUT("/api/v1/workspaces/{workspace_id}/rules/{rule_id}", {
            params: { path: { ...path, rule_id: savedId } },
            body: payload,
          })
        : await api.POST("/api/v1/workspaces/{workspace_id}/rules", {
            params: { path },
            body: payload,
          });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (saved) => {
      setSavedId(saved.id);
      setSavedActive(saved.active);
      setRunning(false);
      await queryClient.invalidateQueries({ queryKey: rulesQuery(workspaceId).queryKey });
    },
  });

  const test = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/rules/test", {
        params: { path: { workspace_id: workspaceId } },
        body: body(),
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

  const run = useMutation({
    mutationFn: async () => {
      if (!savedId) return;
      const { error } = await api.POST("/api/v1/workspaces/{workspace_id}/rules/{rule_id}/run", {
        params: { path: { workspace_id: workspaceId, rule_id: savedId } },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: () => setRunning(true),
  });

  const params = { workspaceId };
  const matched = test.data?.matched_count ?? 0;
  const checked = test.data?.checked_count ?? 0;
  const total = test.data?.total_count ?? 0;

  return (
    <Form
      className="form rule-form"
      onSubmit={(event) => {
        event.preventDefault();
        test.reset();
        run.reset();
        setSavedActive(null);
        save.mutate();
      }}
    >
      <TextField className="field" value={name} onChange={setName} isRequired maxLength={100}>
        <Label>{t`Naam`}</Label>
        <Input />
      </TextField>

      <section className="form-section" aria-label={t`Voorwaarden`}>
        <div className="rule-match">
          <span>{t`Als`}</span>
          <SelectField
            label={t`Hoeveel voorwaarden moeten kloppen`}
            value={match}
            options={[
              { value: "all", text: t`alle` },
              { value: "any", text: t`een van` },
            ]}
            onChange={setMatch}
          />
          <span>{t`deze voorwaarden kloppen`}</span>
        </div>
        {conditions.map((row, index) => (
          <ConditionEditor
            key={row.key}
            row={row}
            number={index + 1}
            choices={choices}
            canRemove={conditions.length > 1}
            onChange={(next) =>
              setConditions(conditions.map((other) => (other.key === row.key ? next : other)))
            }
            onRemove={() => setConditions(conditions.filter((other) => other.key !== row.key))}
          />
        ))}
        <Button
          className="button secondary"
          isDisabled={conditions.length >= 20}
          onPress={() =>
            setConditions([
              ...conditions,
              { key: newKey(), field: "text", op: "contains_word", value: "" },
            ])
          }
        >
          {t`Voorwaarde toevoegen`}
        </Button>
      </section>

      <section className="form-section" aria-label={t`Acties`}>
        <p className="choice-label">{t`Dan`}</p>
        {actions.map((row, index) => (
          <ActionEditor
            key={row.key}
            row={row}
            number={index + 1}
            choices={choices}
            canRemove={actions.length > 1}
            onChange={(next) =>
              setActions(actions.map((other) => (other.key === row.key ? next : other)))
            }
            onRemove={() => setActions(actions.filter((other) => other.key !== row.key))}
          />
        ))}
        <Button
          className="button secondary"
          isDisabled={actions.length >= 10}
          onPress={() =>
            setActions([
              ...actions,
              { key: newKey(), type: "add_label", id: defaultAction("add_label", choices) },
            ])
          }
        >
          {t`Actie toevoegen`}
        </Button>
      </section>

      <section className="form-section rule-switches" aria-label={t`Instellingen`}>
        <Switch
          className="switch"
          isSelected={mode === "auto"}
          onChange={(on) => setMode(on ? "auto" : "propose")}
        >
          <span className="switch-track" aria-hidden="true" />
          {t`Automatisch toepassen`}
        </Switch>
        <p className="panel-note">{t`Uit: de regel doet een voorstel dat jij bevestigt.`}</p>
        <Switch className="switch" isSelected={stop} onChange={setStop}>
          <span className="switch-track" aria-hidden="true" />
          {t`Stop daarna met de volgende regels`}
        </Switch>
        <Switch className="switch" isSelected={active} onChange={setActive}>
          <span className="switch-track" aria-hidden="true" />
          {t`Actief`}
        </Switch>
      </section>

      <ErrorAlert error={save.error ?? test.error ?? run.error} />

      <div className="form-actions">
        <Button type="submit" className="button" isPending={save.isPending}>
          {t`Opslaan`}
        </Button>
        <Button
          className="button secondary"
          isPending={test.isPending}
          onPress={() => {
            save.reset();
            test.mutate();
          }}
        >
          {t`Test op bestaande post`}
        </Button>
        <Link to="/w/$workspaceId/automatisering" params={params} className="button-text">
          {t`Terug naar de regels`}
        </Link>
      </div>

      {test.data ? (
        <section className="panel" aria-label={t`Uitkomst van de test`}>
          <p role="status">{t`${matched} van de nieuwste ${checked} brieven passen`}</p>
          {total > checked ? <p>{t`Je hebt ${total} brieven`}</p> : null}
          <DocumentTable
            workspaceId={workspaceId}
            documents={test.data.documents}
            persons={choices.persons}
            labels={choices.labels}
          />
        </section>
      ) : null}

      {savedActive === true ? (
        <section className="panel" aria-label={t`Toepassen op bestaande post`}>
          <h2>{t`Toepassen op bestaande post?`}</h2>
          <p>{t`De regel wordt op al je post toegepast, niet alleen op de nieuwste 500.`}</p>
          {running ? (
            <p role="status">{t`Bezig. Je ziet het resultaat bij de brieven zelf.`}</p>
          ) : (
            <Button
              className="button"
              isPending={run.isPending}
              onPress={() => {
                run.reset();
                run.mutate();
              }}
            >
              {t`Toepassen`}
            </Button>
          )}
        </section>
      ) : null}
      {savedActive === false ? <p role="status">{t`De regel is opgeslagen.`}</p> : null}
    </Form>
  );
}

function RuleEditorPage({ ruleId }: { ruleId: string | null }) {
  const { workspace } = (ruleId ? ruleRoute : newRuleRoute).useRouteContext();
  usePageTitle(ruleId ? t`Regel wijzigen` : t`Nieuwe regel`);
  const rules = useQuery(rulesQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const persons = useQuery(personsQuery(workspace.id));
  const canEdit = workspace.role === "admin";
  const rule = ruleId ? rules.data?.find((candidate) => candidate.id === ruleId) : undefined;
  const ready = rules.data && labels.data && persons.data;

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{ruleId ? t`Regel wijzigen` : t`Nieuwe regel`}</h1>
      </header>
      {canEdit ? null : <p className="panel-note">{t`Alleen een beheerder kan regels wijzigen`}</p>}
      {canEdit && !ready ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={rules.error ?? labels.error ?? persons.error} />
      {canEdit && ready && ruleId && !rule ? (
        <p role="alert" className="alert">
          {t`Deze regel bestaat niet (meer).`}
        </p>
      ) : null}
      {canEdit && ready && (!ruleId || rule) ? (
        <RuleForm
          key={ruleId ?? "new"}
          workspaceId={workspace.id}
          rule={rule ?? null}
          choices={{
            labels: labels.data ?? [],
            persons: (persons.data ?? []).filter((person) => !person.archived),
          }}
        />
      ) : null}
    </Shell>
  );
}

export const newRuleRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "automatisering/nieuw",
  component: () => <RuleEditorPage ruleId={null} />,
});

export const ruleRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "automatisering/$ruleId",
  component: function RuleRoutePage() {
    const { ruleId } = ruleRoute.useParams();
    return <RuleEditorPage ruleId={ruleId} />;
  },
});
