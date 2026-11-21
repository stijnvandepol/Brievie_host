/**
 * Names compared as the server's name matching compares them (`apps.common.text.normalise`):
 * accents removed, case folded, whitespace runs one space, trimmed. "José  de Vries" and
 * "jose de vries" are the same name.
 */
export function normaliseName(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .replace(/\p{Cf}/gu, "")
    .toLocaleLowerCase("nl")
    .replace(/\s+/g, " ")
    .trim();
}

type Named = { id: string; display_name: string; name_variants: string[]; archived: boolean };

/** How a typed name meets a known person: by their name, or by one of the ways it is written. */
export type NameMatch<T extends Named> = { person: T; variant: string | null };

/**
 * The persons whose name or a variant is exactly `typed` (normalised), the name first; then
 * those whose name merely contains it. Archived persons are not offered.
 */
export function matchName<T extends Named>(
  persons: T[],
  typed: string,
): { exact: NameMatch<T>[]; partial: T[] } {
  const wanted = normaliseName(typed);
  if (!wanted) return { exact: [], partial: [] };
  const exact: NameMatch<T>[] = [];
  const partial: T[] = [];
  for (const person of persons) {
    if (person.archived) continue;
    if (normaliseName(person.display_name) === wanted) {
      exact.unshift({ person, variant: null });
      continue;
    }
    const variant = person.name_variants.find((name) => normaliseName(name) === wanted);
    if (variant !== undefined) {
      exact.push({ person, variant });
      continue;
    }
    if (normaliseName(person.display_name).includes(wanted)) partial.push(person);
  }
  return { exact, partial };
}
