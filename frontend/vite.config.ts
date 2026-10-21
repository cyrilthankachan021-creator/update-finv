/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs locally (default 127.0.0.1:8765); the dev server proxies /api so cookies stay same-origin.
const api = process.env.FINV_API_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5180, strictPort: true, proxy: { "/api": api } },
  preview: { host: "127.0.0.1", port: 4180, strictPort: true, proxy: { "/api": api } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
