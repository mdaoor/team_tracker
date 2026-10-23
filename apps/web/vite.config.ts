/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.TT_API_URL ?? "http://localhost:8000",
      "/healthz": process.env.TT_API_URL ?? "http://localhost:8000",
      "/readyz": process.env.TT_API_URL ?? "http://localhost:8000",
    },
  },
  test: {
    exclude: ["e2e/**", "node_modules/**"],
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
