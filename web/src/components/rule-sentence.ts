import { t } from "@lingui/core/macro";
import type { components } from "../api/schema";
import { typeLabel } from "./document-table";

type LabelOut = components["schemas"]["LabelOut"];
type PersonOut = components["schemas"]["PersonOut"];
type Condition = components["schemas"]["RuleConditionSchema"];
type Action = components["schemas"]["RuleActionSchema"];

/** What a sentence needs of a rule: both `RuleIn` and `RuleOut` fit. */
export type RuleShape = { match: string; conditions: Condition[]; actions: Action[] };

/** The operators of each field (design 9.2), in the order the builder offers them. */
export const FIELD_OPERATORS: Record<string, string[]> = {
  text: ["contains_word", "contains", "not_contains_word"],
  sender: ["contains_word", "contains", "equals"],
  document_type: ["is", "is_not"],
  label: ["has", "has_not"],
  addressee: ["is", "is_empty"],
  source: ["is"],
};

export const FIELDS = ["text", "sender", "document_type", "label", "addressee", "source"];

/** The document type codes the builder offers (the names live in `typeLabel`). */
export const DOCUMENT_TYPES = [
  "invoice",
  "assessment",
  "decision",
  "bill",
  "reminder",
  "demand",
  "policy",
  "annual_statement",
  "payslip",
  "inspection",
  "contract",
  "receipt",
  "information",
  "other",
];

export const SOURCES = ["scan", "upload", "mail", "watch_folder"];

export function sourceLabel(source: string): string {
  switch (source) {
    case "scan":
      return t`Scan`;
    case "upload":
      return t`Upload`;
    case "mail":
      return t`Mail`;
    case "watch_folder":
      return t`Watch-map`;
    default:
      return source;
  }
}

export function fieldLabel(field: string): string {
  switch (field) {
    case "text":
      return t`De tekst`;
    case "sender":
      return t`De afzender`;
    case "document_type":
      return t`Het soort post`;
    case "label":
      return t`Een label`;
    case "addressee":
      return t`Op naam van`;
    case "source":
      return t`De bron`;
    default:
      return field;
  }
}

export function operatorLabel(op: string): string {
  switch (op) {
    case "contains_word":
      return t`bevat het woord`;
    case "contains":
      return t`bevat de tekst`;
    case "not_contains_word":
      return t`bevat niet het woord`;
    case "equals":
      return t`is precies`;
    case "is":
      return t`is`;
    case "is_not":
      return t`is niet`;
    case "has":
      return t`heeft`;
    case "has_not":
      return t`heeft niet`;
    case "is_empty":
      return t`is leeg`;
    default:
      return op;
  }
}

function conditionSentence(
  { field, op, value }: Condition,
  labels: LabelOut[],
  persons: PersonOut[],
): string {
  const gone = t`(verwijderd)`;
  if (field === "text" || field === "sender") {
    const subject = field === "text" ? t`de tekst` : t`de afzender`;
    if (op === "contains_word") return t`${subject} het woord ${value} bevat`;
    if (op === "not_contains_word") return t`${subject} het woord ${value} niet bevat`;
    if (op === "contains") return t`${subject} "${value}" bevat`;
    return t`${subject} "${value}" is`;
  }
  if (field === "document_type") {
    const type = typeLabel(value) || value;
    return op === "is_not" ? t`het soort post niet ${type} is` : t`het soort post ${type} is`;
  }
  if (field === "label") {
    const name = labels.find((label) => label.id === value)?.name ?? gone;
    return op === "has_not"
      ? t`de brief het label ${name} niet heeft`
      : t`de brief het label ${name} heeft`;
  }
  if (field === "addressee") {
    if (op === "is_empty") return t`de brief op niemands naam staat`;
    const name = persons.find((person) => person.id === value)?.display_name ?? gone;
    return t`de brief op naam van ${name} staat`;
  }
  const source = sourceLabel(value);
  return t`de bron ${source} is`;
}

function actionSentence(action: Action, labels: LabelOut[], persons: PersonOut[]): string {
  const gone = t`(verwijderd)`;
  if (action.type === "add_label") {
    const name = labels.find((label) => label.id === action.label_id)?.name ?? gone;
    return t`label ${name}`;
  }
  const name = persons.find((person) => person.id === action.person_id)?.display_name ?? gone;
  return t`op naam van ${name}`;
}

/** Reads a rule back: "Als de tekst het woord APK bevat → label Auto". */
export function ruleSentence(rule: RuleShape, labels: LabelOut[], persons: PersonOut[]): string {
  const joiner = rule.match === "any" ? ` ${t`of`} ` : ` ${t`en`} `;
  const conditions = rule.conditions
    .map((condition) => conditionSentence(condition, labels, persons))
    .join(joiner);
  const actions = rule.actions.map((action) => actionSentence(action, labels, persons)).join(", ");
  return `${t`Als`} ${conditions} → ${actions}`;
}
