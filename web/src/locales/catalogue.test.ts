// The English catalogue has every message of the Dutch source catalogue, translated, with
// the same placeholders. A new Dutch text fails here until it has its English: run
// `pnpm extract` and fill the empty msgstr in src/locales/en/messages.po.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function read(locale: string): Map<string, string> {
  // Vitest runs from web/.
  const path = join(process.cwd(), "src", "locales", locale, "messages.po");
  const entries = new Map<string, string>();
  for (const block of readFileSync(path, "utf8").split(/\n\s*\n/)) {
    const id = /^msgid (".*")$/m.exec(block)?.[1];
    const str = /^msgstr (".*")$/m.exec(block)?.[1];
    if (id === undefined || str === undefined) continue;
    const msgid = JSON.parse(id) as string;
    if (msgid) entries.set(msgid, JSON.parse(str) as string);
  }
  return entries;
}

/** The names of the placeholders ({0}, {name}) and plural arguments, sorted. A plural's
 * branch ("one {brief}") is text, not a placeholder. */
function placeholders(text: string): string[] {
  const found = new Set<string>();
  for (const [, name] of text.matchAll(/(?<!(?:zero|one|two|few|many|other|=\d+) )\{(\w+)(?=[,}])/g)) if (name) found.add(name);
  return [...found].sort();
}

describe("the English catalogue", () => {
  const nl = read("nl");
  const en = read("en");

  it("holds the Dutch messages", () => {
    expect(nl.size).toBeGreaterThan(500);
  });

  it("has an English translation of every Dutch message", () => {
    const missing = [...nl.keys()].filter((msgid) => !en.get(msgid)?.trim());
    expect(missing).toEqual([]);
  });

  it("keeps the placeholders of every message", () => {
    const wrong = [...nl.keys()].filter(
      (msgid) => placeholders(msgid).join() !== placeholders(en.get(msgid) ?? "").join(),
    );
    expect(wrong).toEqual([]);
  });
});
