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
    [{ field: "source", op: "is", value: "watch_folder" }, "de bron Watch-map is"],
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
