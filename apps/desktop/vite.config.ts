/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vite";

// `index.html` is the palette window, `personal.html` the personal tracker window (Phase 15); `mobile.html` is the installable phone app
// (Mobile Lite, ADR-0057: manifest + service worker from `public/`); `demo.html` (in-memory data) is dev-only.
// `TT_BASE=/m/ pnpm build` builds the phone app for a server that mounts it under a prefix (default `/`, which the Tauri shell needs).
export default defineConfig(({ command }) => ({
  base: process.env.TT_BASE ?? "/",
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        personal: resolve(__dirname, "personal.html"),
        mobile: resolve(__dirname, "mobile.html"),
        ...(command === "serve" ? { demo: resolve(__dirname, "demo.html") } : {}),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    exclude: ["e2e/**", "node_modules/**"],
  },
}));
