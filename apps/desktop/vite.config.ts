/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vite";

// `index.html` is the palette window, `personal.html` the personal tracker window (Phase 15); `demo.html` (in-memory data) is dev-only.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        personal: resolve(__dirname, "personal.html"),
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
