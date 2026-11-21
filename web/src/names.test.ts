import { describe, expect, it } from "vitest";
import { matchName, normaliseName } from "./names";

const jan = {
  id: "1",
  display_name: "Jan de Vries",
  name_variants: ["J. de Vries"],
  archived: false,
};
const anne = { id: "2", display_name: "Anne", name_variants: [], archived: false };
const old = { id: "3", display_name: "Jan", name_variants: [], archived: true };

describe("names", () => {
  it("normalises as the server does", () => {
    expect(normaliseName("  José   DE\tVries ")).toBe("jose de vries");
    expect(normaliseName("Ångström")).toBe("angstrom");
    expect(normaliseName("Zoë​")).toBe("zoe");
  });

  it("finds a name, then a variant, never an archived person", () => {
    expect(matchName([jan, anne, old], "jan de vries").exact).toEqual([
      { person: jan, variant: null },
    ]);
    expect(matchName([jan, anne, old], "J. DE VRIES").exact).toEqual([
      { person: jan, variant: "J. de Vries" },
    ]);
    expect(matchName([jan, anne, old], "jan")).toEqual({ exact: [], partial: [jan] });
    expect(matchName([jan, anne, old], "   ")).toEqual({ exact: [], partial: [] });
  });
});
