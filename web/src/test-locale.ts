// Imported first by test-setup.ts, before anything loads ./i18n: the tests read the Dutch
// source texts, whatever language the test browser (jsdom: en-US) prefers.
localStorage.setItem("brievie.language", "nl");
