import { I18nProvider } from "@lingui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { i18n } from "./i18n";
import { LocaleRouter } from "./locale-router";
import { createAppRouter } from "./router";
import "./styles.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const router = createAppRouter(queryClient);

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root");
createRoot(root).render(
  <StrictMode>
    <I18nProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <LocaleRouter router={router} />
      </QueryClientProvider>
    </I18nProvider>
  </StrictMode>,
);
