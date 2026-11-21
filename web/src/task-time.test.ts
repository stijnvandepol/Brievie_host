import { describe, expect, it } from "vitest";
import { setLocale } from "./i18n";
import { amountText, type ForFacts, forText, groupTitle, whenText } from "./task-time";

describe("whenText", () => {
  it.each([
    [0, "2026-11-21", "Vandaag"],
    [1, "2026-11-22", "Morgen"],
    [2, "2026-11-23", "Nog 2 dagen (23 nov)"],
    [10, "2026-12-01", "Nog 10 dagen (1 dec)"],
    [-1, "2026-11-20", "1 dag te laat (20 nov)"],
    [-3, "2026-11-18", "3 dagen te laat (18 nov)"],
  ])("%i days left on %s is %s", (days, deadline, words) => {
    expect(whenText(days, deadline)).toBe(words);
  });

  it("says nothing without a date", () => {
    expect(whenText(null, null)).toBe("");
  });

  it("speaks English", () => {
    setLocale("en");
    expect(whenText(-1, "2026-11-20")).toMatch(/^1 day overdue/);
    expect(whenText(-3, "2026-11-18")).toMatch(/^3 days overdue/);
    expect(whenText(0, "2026-11-21")).toBe("Today");
  });
});

describe("groupTitle", () => {
  it("names every group", () => {
    expect(
      ["overdue", "within_week", "later", "no_date"].map((g) => groupTitle(g as never)),
    ).toEqual(["Te laat", "Binnen 7 dagen", "Later", "Zonder datum"]);
  });
});

describe("amountText", () => {
  it("writes euros the Dutch way", () => {
    // Intl puts a no-break space after the sign: the amount never wraps.
    const plain = (value: string) => amountText(value).replace(/\s/g, " ");
    expect(plain("1234.56")).toBe("€ 1.234,56");
    expect(plain("0.00")).toBe("€ 0,00");
    expect(amountText("")).toBe("");
  });
});

describe("forText", () => {
  const base: ForFacts = { reason: "", for_names: [], assigned_to_name: "", assigned_by_name: "" };
  it.each<[Partial<ForFacts>, string]>([
    [{ reason: "addressee" }, "Voor jou"],
    [{ reason: "addressee", for_names: ["Piet"] }, "Voor jou en Piet"],
    [{ reason: "department", for_names: ["Finance"] }, "Voor Finance"],
    [{ reason: "unassigned" }, "Zonder naam"],
    [{ reason: "unassigned", for_names: ["Jan"] }, "Voor Jan"],
    [{ reason: "assigned", assigned_by_name: "Bas" }, "Aan jou gegeven door Bas"],
    [{ reason: "assigned", assigned_by_name: "Anna" }, "Jij doet dit"],
    [{ reason: "other", for_names: ["Piet", "Jan", "Bas"] }, "Voor Piet, Jan en Bas"],
    [{ reason: "other", assigned_to_name: "Piet", for_names: ["Jan"] }, "Bij Piet"],
  ])("%o is %s", (facts, words) => {
    expect(forText({ ...base, ...facts }, "Anna")).toBe(words);
  });
});
