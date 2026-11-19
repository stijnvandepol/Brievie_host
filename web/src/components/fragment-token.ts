import { useLocation, useRouter } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

/**
 * Reads a one-time token from the URL fragment (`/uitnodiging#<token>`), once, and takes it
 * out of the address bar and the history before anything else happens: `onToken` runs after
 * the address is clean, so no request, log line or reload can carry the token from here on.
 * The token only lives in this hook's state; it never goes into a URL, storage or a query key.
 * An empty string means the address had no fragment.
 */
export function useFragmentToken(onToken: (token: string) => void) {
  const router = useRouter();
  const hash = useLocation({ select: (location) => location.hash });
  // Read at the first render; a later change of the router's location (the clean-up below)
  // must not lose it.
  const [token] = useState(() => hash.replace(/^#/, ""));
  const callback = useRef(onToken);
  callback.current = onToken;
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return;
    done.current = true;
    window.history.replaceState(null, "", window.location.pathname);
    // The router keeps its own copy of the address: bring it in line (a replace, so the
    // token is in no history entry either).
    router.history.replace(router.state.location.pathname);
    callback.current(token);
  }, [router, token]);
}
