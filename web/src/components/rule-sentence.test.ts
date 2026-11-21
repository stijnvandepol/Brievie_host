import { describe, expect, it } from "vitest";
import { annaPerson, belasting, papa, sales } from "../test-utils";
import { type RuleShape, ruleSentence } from "./rule-sentence";

const labels = [belasting, sales];
const persons = [papa, annaPerson];

function rule(patch: Partial<RuleShape>): RuleShape {
  return {
    match: "all",
    conditions: [{ field: "text", op: "contains_word", value: "APK" }],
    actions: [{ type: "add_label", label_id: sales.id }],
    ...patch,
  };
}

describe("ruleSentence", () => {
  it("reads a rule back in Dutch", () => {
    expect(ruleSentence(rule({}), labels, persons)).toBe(
      "Als de tekst het woord APK bevat → label Sales",
    );
  });

  it("joins conditions with en (all) or of (any)", () => {
    const conditions = [
      { field: "text", op: "contains_word", value: "APK" },
      { field: "sender", op: "contains", value: "Garage" },
    ];
    expect(ruleSentence(rule({ conditions }), labels, persons)).toBe(
      'Als de tekst het woord APK bevat en de afzender "Garage" bevat → label Sales',
    );
    expect(ruleSentence(rule({ conditions, match: "any" }), labels, persons)).toBe(
      'Als de tekst het woord APK bevat of de afzender "Garage" bevat → label Sales',
    );
  });

  it("groups consecutive conditions with the same field and operator", () => {
    const word = (value: string) => ({ field: "text", op: "contains_word", value });
    expect(
      ruleSentence(
        rule({
          match: "any",
          conditions: [word("belastingdienst"), word("toeslagen")],
          actions: [{ type: "add_label", label_id: belasting.id }],
        }),
        labels,
        persons,
      ),
    ).toBe("Als de tekst het woord belastingdienst of toeslagen bevat → label Belastingdienst");
    expect(
      ruleSentence(
        rule({
          match: "any",
          conditions: [word("zorgverzekering"), word("CAK"), word("huisarts")],
        }),
        labels,
        persons,
      ),
    ).toBe("Als de tekst het woord zorgverzekering, CAK of huisarts bevat → label Sales");
    expect(ruleSentence(rule({ conditions: [word("APK"), word("garage")] }), labels, persons)).toBe(
      "Als de tekst het woord APK en garage bevat → label Sales",
    );
  });

  it("keeps the joining between groups of different field or operator", () => {
    const conditions = [
      { field: "text", op: "contains_word", value: "APK" },
      { field: "text", op: "contains_word", value: "garage" },
      { field: "sender", op: "contains", value: "Auto" },
      { field: "sender", op: "contains", value: "Bandenhuis" },
      { field: "text", op: "contains", value: "factuur" },
    ];
    expect(ruleSentence(rule({ conditions, match: "any" }), labels, persons)).toBe(
      'Als de tekst het woord APK of garage bevat of de afzender "Auto" of "Bandenhuis" bevat of de tekst "factuur" bevat → label Sales',
    );
  });

  it("groups every operator with a value or a choice", () => {
    const read = (conditions: { field: string; op: string; value: string }[]) =>
      ruleSentence(rule({ conditions, match: "any" }), labels, persons).slice(
        4,
        -" → label Sales".length,
      );
    expect(
      read([
        { field: "text", op: "not_contains_word", value: "a" },
        { field: "text", op: "not_contains_word", value: "b" },
      ]),
    ).toBe("de tekst het woord a of b niet bevat");
    expect(
      read([
        { field: "sender", op: "equals", value: "a" },
        { field: "sender", op: "equals", value: "b" },
      ]),
    ).toBe('de afzender "a" of "b" is');
    expect(
      read([
        { field: "document_type", op: "is", value: "invoice" },
        { field: "document_type", op: "is", value: "bill" },
      ]),
    ).toBe("het soort post Factuur of Rekening is");
    expect(
      read([
        { field: "document_type", op: "is_not", value: "invoice" },
        { field: "document_type", op: "is_not", value: "bill" },
      ]),
    ).toBe("het soort post niet Factuur of Rekening is");
    expect(
      read([
        { field: "label", op: "has", value: belasting.id },
        { field: "label", op: "has", value: sales.id },
      ]),
    ).toBe("de brief het label Belastingdienst of Sales heeft");
    expect(
      read([
        { field: "label", op: "has_not", value: belasting.id },
        { field: "label", op: "has_not", value: sales.id },
      ]),
    ).toBe("de brief het label Belastingdienst of Sales niet heeft");
  });

  it.each([
    [{ field: "text", op: "contains", value: "APK" }, 'de tekst "APK" bevat'],
    [{ field: "text", op: "not_contains_word", value: "APK" }, "de tekst het woord APK niet bevat"],
    [
      { field: "sender", op: "contains_word", value: "Garage" },
      "de afzender het woord Garage bevat",
    ],
    [{ field: "sender", op: "equals", value: "Garage" }, 'de afzender "Garage" is'],
    [{ field: "document_type", op: "is", value: "invoice" }, "het soort post Factuur is"],
    [{ field: "document_type", op: "is_not", value: "invoice" }, "het soort post niet Factuur is"],
    [
      { field: "label", op: "has", value: belasting.id },
      "de brief het label Belastingdienst heeft",
    ],
    [
      { field: "label", op: "has_not", value: belasting.id },
      "de brief het label Belastingdienst niet heeft",
    ],
    [{ field: "addressee", op: "is", value: papa.id }, "de brief op naam van Papa staat"],
    [{ field: "addressee", op: "is_empty", value: "" }, "de brief op niemands naam staat"],
    [{ field: "source", op: "is", value: "watch_folder" }, "de bron Bewaakte map is"],
  ])("reads %j", (condition, text) => {
    expect(ruleSentence(rule({ conditions: [condition] }), labels, persons)).toBe(
      `Als ${text} → label Sales`,
    );
  });

  it("reads both kinds of action", () => {
    const actions = [
      { type: "add_label", label_id: sales.id },
      { type: "add_addressee", person_id: papa.id },
    ];
    expect(ruleSentence(rule({ actions }), labels, persons)).toBe(
      "Als de tekst het woord APK bevat → label Sales, op naam van Papa",
    );
  });

  it("the sentence says maak de brief privé", () => {
    const actions = [{ type: "make_private", label_id: null, person_id: null }];
    expect(ruleSentence(rule({ actions }), labels, persons)).toBe(
      "Als de tekst het woord APK bevat → maak de brief privé",
    );
  });

  it("says (verwijderd) for a label or person that no longer exists", () => {
    const gone = "e9999999-9999-4999-8999-999999999999";
    expect(
      ruleSentence(
        rule({
          conditions: [{ field: "label", op: "has", value: gone }],
          actions: [
            { type: "add_label", label_id: gone },
            { type: "add_addressee", person_id: gone },
          ],
        }),
        labels,
        persons,
      ),
    ).toBe(
      "Als de brief het label (verwijderd) heeft → label (verwijderd), op naam van (verwijderd)",
    );
  });
});
