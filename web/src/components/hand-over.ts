import { t } from "@lingui/core/macro";
import type { components } from "../api/schema";

type Person = components["schemas"]["PersonOut"];
type Member = components["schemas"]["MemberOut"];
type Visibility = components["schemas"]["VisibilityOut"];

/** What the web knows about the caller's own sight (S 4.1), enough to warn before a letter
 * goes to somebody else. The server decides; this only says it beforehand. */
export type OwnSight = {
  /** Sees every letter that is not private (everyone sees all, or an admin who reads all). */
  seesAll: boolean;
  /** Sorts post: gets what carries no name anybody reads. */
  sorts: boolean;
  /** The caller's own person and the departments they are in (while departments are on). */
  mine: Set<string>;
  /** The departments the caller is in (while departments are on), for the chips. */
  departments: Person[];
  ownPersonId: string | null;
};

export function ownSight(
  visibility: Visibility | undefined,
  role: string,
  userId: string | undefined,
  members: Member[],
  persons: Person[],
): OwnSight | null {
  if (!visibility || !userId) return null;
  const me = members.find((member) => member.user_id === userId);
  const ownPersonId = me?.person_id ?? null;
  const departments = visibility.departments_enabled
    ? persons.filter(
        (person) =>
          person.kind === "department" &&
          !person.archived &&
          (person.member_user_ids ?? []).includes(userId),
      )
    : [];
  const mine = new Set<string>(departments.map((department) => department.id));
  if (ownPersonId) mine.add(ownPersonId);
  const sorters = visibility.sorter_user_ids;
  return {
    seesAll: visibility.everyone_sees_all || (role === "admin" && visibility.admins_read_all),
    sorts: sorters.includes(userId) || (role === "admin" && sorters.length === 0),
    mine,
    departments,
    ownPersonId,
  };
}

/** Whether somebody reads the post of this person or department (an account, or members
 * while departments are on); post on nobody's name goes to the sorters. */
function covered(person: Person | undefined, departmentsEnabled: boolean): boolean {
  if (!person) return false;
  if (person.kind === "department") {
    return departmentsEnabled && (person.member_user_ids ?? []).length > 0;
  }
  return person.user_id !== null;
}

/** Whether the caller would no longer see a letter (not private) put on `chosen`. */
export function losesLetter(
  sight: OwnSight | null,
  chosen: string[],
  persons: Person[],
  departmentsEnabled: boolean,
): boolean {
  if (!sight || sight.seesAll || chosen.length === 0) return false;
  if (chosen.some((id) => sight.mine.has(id))) return false;
  const anyCovered = chosen.some((id) =>
    covered(
      persons.find((person) => person.id === id),
      departmentsEnabled,
    ),
  );
  // Nobody reads it: it stays with the sorters.
  return !(sight.sorts && !anyCovered);
}

function names(persons: Person[], ids: string[]): string {
  const list = ids.flatMap((id) => {
    const person = persons.find((candidate) => candidate.id === id);
    return person ? [person.display_name] : [];
  });
  if (list.length <= 1) return list[0] ?? "";
  const last = list[list.length - 1] ?? "";
  const rest = list.slice(0, -1).join(", ");
  return t`${rest} en ${last}`;
}

/**
 * The sentence before the letter goes (S 4.5, 4.8): moving it from one of your departments
 * to another says the department ("Na toewijzen ziet HR deze brief niet meer. Hij gaat naar
 * Finance."); otherwise it says you.
 */
export function handOverSentence(
  sight: OwnSight,
  before: string[],
  chosen: string[],
  persons: Person[],
): string {
  const target = names(persons, chosen);
  const leaving = before.filter(
    (id) => sight.mine.has(id) && id !== sight.ownPersonId && !chosen.includes(id),
  );
  if (leaving.length > 0) {
    const from = names(persons, leaving);
    return t`Na toewijzen ziet ${from} deze brief niet meer. Hij gaat naar ${target}.`;
  }
  return t`Na opslaan kun je deze brief niet meer zien. Hij gaat naar ${target}.`;
}

/** What a read letter without any name waits for: a name, or with departments on, a place. */
export function assignWord(departmentsEnabled: boolean): string {
  return departmentsEnabled ? t`Toewijzen` : t`Naam kiezen`;
}
