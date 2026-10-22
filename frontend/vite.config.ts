import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Local development only. In production the reverse proxy (deploy/Caddyfile) routes /api.
const apiProxy = { "/api": { target: process.env.CB_API_ORIGIN ?? "http://127.0.0.1:8000", changeOrigin: false } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: apiProxy },
  preview: { port: 4173, proxy: apiProxy },
  build: { sourcemap: false, target: "es2022", chunkSizeWarningLimit: 700 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
