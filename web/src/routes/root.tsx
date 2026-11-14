import { t } from "@lingui/core/macro";
import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  type ErrorComponentProps,
  Link,
  Outlet,
  useRouter,
  useRouterState,
} from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { Button } from "react-aria-components";
import { ErrorAlert, Plain, usePageTitle } from "../components/page";

/** After a route change, screen reader and keyboard users start at the new page's title. */
function RootComponent() {
  const pathname = useRouterState({ select: (state) => state.resolvedLocation?.pathname });
  const previous = useRef<string | undefined>(undefined);
  useEffect(() => {
    const before = previous.current;
    previous.current = pathname;
    // The first page load keeps the browser's own focus.
    if (before === undefined || before === pathname) return;
    const heading = document.querySelector("h1");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    }
  }, [pathname]);
  return <Outlet />;
}

function RouteError({ error, reset }: ErrorComponentProps) {
  usePageTitle(t`Er ging iets mis`);
  const router = useRouter();
  async function retry() {
    await router.invalidate();
    reset();
  }
  return (
    <Plain>
      <h1>{t`Er ging iets mis`}</h1>
      <ErrorAlert error={error} />
      <Button className="button" onPress={retry}>
        {t`Probeer opnieuw`}
      </Button>
    </Plain>
  );
}

function RouteNotFound() {
  usePageTitle(t`Pagina niet gevonden`);
  return (
    <Plain>
      <h1>{t`Deze pagina bestaat niet.`}</h1>
      <p>
        <Link to="/">{t`Naar de startpagina`}</Link>
      </p>
    </Plain>
  );
}

export const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootComponent,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});
