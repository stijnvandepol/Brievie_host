import { t } from "@lingui/core/macro";
import { useId } from "react";
import { Badge, MessageBar } from "./ui";

/** "Anne", "Anne en Bob", "Anne, Bob en Carla". */
function listNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  const last = names[names.length - 1] ?? "";
  const rest = names.slice(0, -1).join(", ");
  return t`${rest} en ${last}`;
}

/** Who may read a private letter, as a sentence ("Privé" alone when nobody is named). */
export function privateSentence(readerNames: string[]): string {
  if (readerNames.length === 0) return t`Privé`;
  const names = listNames(readerNames);
  return t`Privé voor ${names}`;
}

/**
 * The lock of a private letter: in a list a "Privé" badge whose description names the
 * readers; on the letter a bar "Privé voor Anne en Bob". A letter that is not private, or
 * one from a server that does not know `visibility`, shows nothing.
 */
export function PrivateLock({
  visibility,
  readerNames,
  variant,
}: {
  visibility: string | undefined;
  readerNames: string[] | undefined;
  variant: "row" | "page";
}) {
  const id = useId();
  if (visibility !== "restricted") return null;
  const sentence = privateSentence(readerNames ?? []);
  if (variant === "page") {
    return <MessageBar intent="info">{sentence}</MessageBar>;
  }
  return (
    <>
      <span aria-describedby={id}>
        <Badge icon="lock">{t`Privé`}</Badge>
      </span>
      <span id={id} className="visually-hidden">
        {sentence}
      </span>
    </>
  );
}
