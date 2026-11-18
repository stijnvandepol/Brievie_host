import { plural, t } from "@lingui/core/macro";
import { useEffect, useState } from "react";

/** Seconds left until `expiresAt`, counting down; 0 means expired. */
export function useSecondsLeft(expiresAt: string | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [expiresAt]);
  return expiresAt ? Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000)) : 0;
}

/** "9 minuten en 58 seconden", "1 minuut", "45 seconden". */
export function formatTimeLeft(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const minutesText = plural(minutes, { one: "# minuut", other: "# minuten" });
  const secondsText = plural(seconds, { one: "# seconde", other: "# seconden" });
  if (minutes === 0) return secondsText;
  if (seconds === 0) return minutesText;
  return t`${minutesText} en ${secondsText}`;
}
