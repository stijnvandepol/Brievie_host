import { i18n } from "@lingui/core";
import { messages } from "./locales/nl/messages.po";

// Dutch is the only language of the web interface for now; the catalogue is the
// extracted source text (`pnpm extract`).
i18n.load("nl", messages);
i18n.activate("nl");

export { i18n };
