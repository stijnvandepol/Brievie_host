import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";
import { Button, Form, Input, Label, Switch, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { labelsQuery, personsQuery, rulesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { DocumentTable, typeLabel } from "../components/document-table";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import {
  DOCUMENT_TYPES,
  FIELD_OPERATORS,
  FIELDS,
  fieldLabel,
  operatorLabel,
  SOURCES,
  sourceLabel,
} from "../components/rule-sentence";
import { Loading, MessageBar } from "../components/ui";
import { workspaceRoute } from "./workspace";

type RuleOut = components["schemas"]["RuleOut"];
type RuleBody = components["schemas"]["RuleIn"];
type LabelOut = components["schemas"]["LabelOut"];
type PersonOut = components["schemas"]["PersonOut"];

type ConditionRow = { key: number; field: string; op: string; value: string };
type ActionRow = { key: number; type: string; id: string };

/** `persons` are the active ones; `everyone` also holds the archived, to name a saved value. */
type Choices = { labels: LabelOut[]; persons: PersonOut[]; everyone: PersonOut[] };

type Option = { value: string; text: string; disabled?: boolean };

/** The current value as an extra option when the list no longer holds it, so what shows is what is saved. */
function withCurrent(options: Option[], value: string, text: () => string): Option[] {
  return value === "" || options.some((option) => option.value === value)
    ? options
    : [...options, { value, text: text() }];
}

function personOptions(choices: Choices, value: string): Option[] {
  const options = choices.persons.map((person) => ({
    value: person.id,
    text: person.display_name,
  }));
  return withCurrent(options, value, () => {
    const known = choices.everyone.find((person) => person.id === value);
    return known ? `${known.display_name} ${t`(gearchiveerd)`}` : t`(verwijderd)`;
  });
}

function labelOptions(choices: Choices, value: string): Option[] {
  const options = choices.labels.map((label) => ({ value: label.id, text: label.name }));
  return withCurrent(options, value, () => t`(verwijderd)`);
}

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

/** A new action on a type that has choices: a label first, else a person. */
function newAction(choices: Choices): { type: string; id: string } {
  const type =
    choices.labels.length === 0 && choices.persons.length > 0 ? "add_addressee" : "add_label";
  return { type, id: defaultAction(type, choices) };
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
  options: Option[];
}) {
  return (
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
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
  const valueOptions: Record<string, Option[]> = {
    document_type: DOCUMENT_TYPES.map((type) => ({ value: type, text: typeLabel(type) })),
    label: labelOptions(choices, row.value),
    addressee: personOptions(choices, row.value),
    source: SOURCES.map((source) => ({ value: source, text: sourceLabel(source) })),
  };
  const options = valueOptions[row.field];
  return (
    <fieldset className="rule-row">
      <legend className="visually-hidden">{t`Voorwaarde ${number}`}</legend>
      <SelectField
        label={t`Veld`}
        value={row.field}
        options={FIELDS.map((field) => ({
          value: field,
          text: fieldLabel(field),
          disabled:
            (field === "label" && choices.labels.length === 0) ||
            (field === "addressee" && choices.persons.length === 0),
        }))}
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
        onChange={(op) => {
          // Only a switch to or from "is leeg" changes what the value can be.
          const value =
            op === "is_empty"
              ? ""
              : row.op === "is_empty"
                ? defaultValue(row.field, op, choices)
                : row.value;
          onChange({ ...row, op, value });
        }}
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
        <Icon name="delete" />
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
          { value: "add_label", text: t`Label toevoegen`, disabled: choices.labels.length === 0 },
          {
            value: "add_addressee",
            text: t`Op naam zetten van`,
            disabled: choices.persons.length === 0,
          },
        ]}
        onChange={(type) => onChange({ ...row, type, id: defaultAction(type, choices) })}
      />
      <SelectField
        label={isLabel ? t`Label` : t`Persoon`}
        value={row.id}
        options={isLabel ? labelOptions(choices, row.id) : personOptions(choices, row.id)}
        onChange={(id) => onChange({ ...row, id })}
      />
      <Button
        className="button-text danger"
        aria-label={t`Actie ${number} verwijderen`}
        isDisabled={!canRemove}
        onPress={onRemove}
      >
        <Icon name="delete" />
        {t`Verwijderen`}
      </Button>
    </fieldset>
  );
}

function RuleForm({
  workspaceId,
  rule,
  choices,
  justCreated,
}: {
  workspaceId: string;
  rule: RuleOut | null;
  choices: Choices;
  /** The page was opened by the first save of this rule: its "apply to existing post" offer stays. */
  justCreated: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
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
      : [{ key: newKey(), ...newAction(choices) }],
  );
  // A rule that was just created is changed, not created again, by the next save.
  const [savedId, setSavedId] = useState<string | null>(rule?.id ?? null);
  const [savedActive, setSavedActive] = useState<boolean | null>(
    justCreated && rule ? rule.active : null,
  );
  const [running, setRunning] = useState(false);
  // The flag has done its job (the offer is in state): a reload must not show the offer again.
  // biome-ignore lint/correctness/useExhaustiveDependencies: once, on mount.
  useEffect(() => {
    if (!justCreated || !rule) return;
    void navigate({
      to: "/w/$workspaceId/automatisering/$ruleId",
      params: { workspaceId, ruleId: rule.id },
      search: {},
      replace: true,
    });
  }, []);

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

  const [blocked, setBlocked] = useState<string | null>(null);

  /** Whether the form can go to the server; else why not, as a sentence. */
  function check(): boolean {
    const empty =
      conditions.some(
        (row) =>
          (row.field === "label" || (row.field === "addressee" && row.op !== "is_empty")) &&
          row.value === "",
      ) || actions.some((row) => row.id === "");
    if (choices.labels.length === 0 && choices.persons.length === 0) {
      setBlocked(t`Maak eerst een label of voeg een persoon toe.`);
      return false;
    }
    if (empty) {
      setBlocked(t`Kies bij elke voorwaarde en actie een label of persoon.`);
      return false;
    }
    setBlocked(null);
    return true;
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
      // A reload must not show an empty form, and a second save must change this rule.
      if (!rule) {
        await navigate({
          to: "/w/$workspaceId/automatisering/$ruleId",
          params: { workspaceId, ruleId: saved.id },
          replace: true,
          search: { opgeslagen: true },
        });
      }
    },
  });

  // Whether the last test left letters out; it outlives `test.reset()` on save.
  const [limited, setLimited] = useState(false);
  const test = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/rules/test", {
        params: { path: { workspace_id: workspaceId } },
        body: body(),
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (data) => setLimited(data.total_count > data.checked_count),
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

  // A result belongs to the form it tested: any edit makes it stale.
  const formKey = JSON.stringify(body());
  const { reset: resetTest } = test;
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key is the trigger.
  useEffect(() => {
    resetTest();
    setBlocked(null);
    setLimited(false);
  }, [formKey, resetTest]);

  const resultRef = useRef<HTMLElement>(null);
  const hasResult = test.data !== undefined;
  useEffect(() => {
    if (hasResult && typeof resultRef.current?.scrollIntoView === "function") {
      resultRef.current.scrollIntoView({ block: "nearest" });
    }
  }, [hasResult]);

  const params = { workspaceId };
  const formId = useId();
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
        if (check()) save.mutate();
      }}
    >
      <section className="builder-section" aria-labelledby={`${formId}-name`}>
        <h2 id={`${formId}-name`} className="builder-title">
          {t`Regel`}
        </h2>
        <TextField className="field" value={name} onChange={setName} isRequired maxLength={100}>
          <Label>{t`Naam`}</Label>
          <Input />
        </TextField>
      </section>

      <section className="builder-section" aria-labelledby={`${formId}-if`}>
        <h2 id={`${formId}-if`} className="builder-title">
          {t`Voorwaarden`}
        </h2>
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
          className="button-text"
          isDisabled={conditions.length >= 20}
          onPress={() =>
            setConditions([
              ...conditions,
              { key: newKey(), field: "text", op: "contains_word", value: "" },
            ])
          }
        >
          <Icon name="add" />
          {t`Voorwaarde toevoegen`}
        </Button>
      </section>

      <section className="builder-section" aria-labelledby={`${formId}-then`}>
        <h2 id={`${formId}-then`} className="builder-title">
          {t`Acties`}
        </h2>
        <p className="rule-then">{t`Dan`}</p>
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
          className="button-text"
          isDisabled={actions.length >= 10}
          onPress={() => setActions([...actions, { key: newKey(), ...newAction(choices) }])}
        >
          <Icon name="add" />
          {t`Actie toevoegen`}
        </Button>
      </section>

      <section className="builder-section rule-switches" aria-labelledby={`${formId}-settings`}>
        <h2 id={`${formId}-settings`} className="builder-title">
          {t`Instellingen`}
        </h2>
        <Switch
          className="switch"
          isSelected={mode === "auto"}
          onChange={(on) => setMode(on ? "auto" : "propose")}
        >
          <span className="switch-track" aria-hidden="true" />
          {t`Automatisch toepassen`}
        </Switch>
        <p className="switch-hint">{t`Uit: de regel doet een voorstel dat jij bevestigt.`}</p>
        <Switch className="switch" isSelected={stop} onChange={setStop}>
          <span className="switch-track" aria-hidden="true" />
          {t`Stop daarna met de volgende regels`}
        </Switch>
        <Switch className="switch" isSelected={active} onChange={setActive}>
          <span className="switch-track" aria-hidden="true" />
          {t`Actief`}
        </Switch>
      </section>

      {choices.labels.length === 0 ? (
        <MessageBar intent="info">{t`Maak eerst een label.`}</MessageBar>
      ) : null}
      {choices.persons.length === 0 ? (
        <MessageBar intent="info">{t`Voeg eerst een persoon toe.`}</MessageBar>
      ) : null}
      {blocked ? (
        <MessageBar intent="error" role="alert">
          {blocked}
        </MessageBar>
      ) : null}
      <ErrorAlert error={save.error ?? test.error ?? run.error} />

      <div className="form-actions">
        <Button type="submit" className="button" isPending={save.isPending}>
          <Icon name="save" />
          {t`Opslaan`}
        </Button>
        <Button
          className="button secondary"
          isPending={test.isPending}
          onPress={() => {
            save.reset();
            if (check()) test.mutate();
          }}
        >
          <Icon name="beaker" />
          {t`Test op bestaande post`}
        </Button>
        <Link to="/w/$workspaceId/automatisering" params={params} className="button-text">
          {t`Terug naar de regels`}
        </Link>
      </div>

      {test.data ? (
        <section
          ref={resultRef}
          className="test-result"
          role="status"
          aria-label={t`Uitkomst van de test`}
        >
          <p className="test-result-count">
            {plural(matched, {
              one: `# van de nieuwste ${checked} brieven past`,
              other: `# van de nieuwste ${checked} brieven passen`,
            })}
          </p>
          {total > checked ? <p className="muted">{t`Je hebt ${total} brieven`}</p> : null}
          <DocumentTable
            workspaceId={workspaceId}
            documents={test.data.documents}
            persons={choices.everyone}
            labels={choices.labels}
            empty={t`Geen brieven passen.`}
          />
        </section>
      ) : null}

      {savedActive === true ? (
        <section
          className="panel panel-pad apply-offer"
          aria-label={t`Toepassen op bestaande post`}
        >
          <h2>{t`Toepassen op bestaande post?`}</h2>
          <p>
            {limited
              ? t`De regel wordt op al je post toegepast, niet alleen op de nieuwste 500.`
              : t`De regel wordt op al je post toegepast.`}
          </p>
          {running ? (
            <MessageBar intent="info" role="status">
              {t`Bezig. Je ziet het resultaat bij de brieven zelf.`}
            </MessageBar>
          ) : (
            <Button
              className="button"
              isPending={run.isPending}
              onPress={() => {
                run.reset();
                run.mutate();
              }}
            >
              <Icon name="play" />
              {t`Toepassen`}
            </Button>
          )}
        </section>
      ) : null}
      {savedActive === false ? (
        <MessageBar intent="success" role="status">
          {t`De regel is opgeslagen.`}
        </MessageBar>
      ) : null}
    </Form>
  );
}

function RuleEditorPage({ ruleId, justCreated }: { ruleId: string | null; justCreated: boolean }) {
  const { workspace } = (ruleId ? ruleRoute : newRuleRoute).useRouteContext();
  usePageTitle(ruleId ? t`Regel wijzigen` : t`Nieuwe regel`);
  const canEdit = workspace.role === "admin";
  // Only an admin gets the form; a viewer may not even read the rules (403).
  const rules = useQuery({ ...rulesQuery(workspace.id), enabled: canEdit });
  const labels = useQuery({ ...labelsQuery(workspace.id), enabled: canEdit });
  const persons = useQuery({ ...personsQuery(workspace.id), enabled: canEdit });
  const rule = ruleId ? rules.data?.find((candidate) => candidate.id === ruleId) : undefined;
  const ready = rules.data && labels.data && persons.data;

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={ruleId ? t`Regel wijzigen` : t`Nieuwe regel`}
        breadcrumb={{
          to: "/w/$workspaceId/automatisering",
          params: { workspaceId: workspace.id },
          label: t`Automatisering`,
        }}
      />
      {canEdit ? null : (
        <MessageBar intent="info">{t`Alleen een beheerder kan regels wijzigen.`}</MessageBar>
      )}
      {canEdit && !ready ? <Loading /> : null}
      <ErrorAlert error={rules.error ?? labels.error ?? persons.error} />
      {canEdit && ready && ruleId && !rule ? (
        <MessageBar intent="error" role="alert">
          {t`Deze regel bestaat niet (meer).`}
        </MessageBar>
      ) : null}
      {canEdit && ready && (!ruleId || rule) ? (
        <RuleForm
          key={ruleId ?? "new"}
          workspaceId={workspace.id}
          rule={rule ?? null}
          justCreated={justCreated}
          choices={{
            labels: labels.data ?? [],
            persons: (persons.data ?? []).filter((person) => !person.archived),
            everyone: persons.data ?? [],
          }}
        />
      ) : null}
    </Shell>
  );
}

export const newRuleRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "automatisering/nieuw",
  component: () => <RuleEditorPage ruleId={null} justCreated={false} />,
});

export const ruleRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "automatisering/$ruleId",
  // `opgeslagen`: the page was reached by the first save of a new rule.
  validateSearch: (search: Record<string, unknown>): { opgeslagen?: true } =>
    search.opgeslagen === true || search.opgeslagen === "true" ? { opgeslagen: true } : {},
  component: function RuleRoutePage() {
    const { ruleId } = ruleRoute.useParams();
    const { opgeslagen } = ruleRoute.useSearch();
    return <RuleEditorPage ruleId={ruleId} justCreated={opgeslagen === true} />;
  },
});
