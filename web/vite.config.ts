import { lingui } from "@lingui/vite-plugin";
import babel from "@rolldown/plugin-babel";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    react(),
    // Lingui's macros (t, Trans) are compiled away by this Babel plugin.
    babel({ plugins: ["@lingui/babel-plugin-lingui-macro"] }),
    lingui(),
  ],
  server: {
    // `pnpm dev` talks to a running Compose stack; its private CA is not trusted here.
    proxy: {
      "/api": { target: process.env.BRIEVIE_DEV_API ?? "https://localhost", secure: false },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
    testTimeout: 15000,
  },
});
