import { describe, expect, it } from "vitest";
import "../i18n";
import { AUDIT_CODES, auditLabel } from "./audit-codes";
import { auditParams } from "./audit-table";

describe("audit table helpers", () => {
  it("puts only the set filters in the query", () => {
    expect(auditParams({ userId: "", code: "", since: "", until: "" }).toString()).toBe("");
    expect(
      auditParams({ userId: "u-1", code: "rule_run", since: "2026-11-01", until: "" }).toString(),
    ).toBe("user_id=u-1&code=rule_run&since=2026-11-01");
  });

  it("labels every known code in Dutch and falls back to the server's label", () => {
    for (const code of AUDIT_CODES) expect(auditLabel(code, "")).not.toBe(code);
    expect(auditLabel("member_removed", "x")).toBe("Lid verwijderd");
    expect(auditLabel("a_newer_code", "Iets nieuws")).toBe("Iets nieuws");
    expect(auditLabel("a_newer_code", "")).toBe("a_newer_code");
  });
});
