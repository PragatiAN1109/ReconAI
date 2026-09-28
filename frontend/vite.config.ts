import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Neither backend sends CORS headers, and neither should be changed for a UI
 * phase. The dev server proxies instead, so the browser only ever makes
 * same-origin requests and no backend configuration is touched.
 *
 * Two prefixes because ReconAI genuinely has two services with two owners:
 * the Spring Financial Core holds the authoritative financial records, and the
 * Python Investigation Service holds investigations, recommendations, reviews
 * and audit. Collapsing them behind one prefix would hide a boundary the whole
 * product rests on.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/core": {
        target: "http://localhost:8099",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/core/, "/api/v1"),
      },
      "/api/investigation": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/investigation/, "/api/v1"),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
