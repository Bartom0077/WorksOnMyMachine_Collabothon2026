import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Mirrors the production nginx config: /api/* is proxied to the backend with the /api prefix stripped.
const backendUrl = process.env.VITE_BACKEND_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        timeout: 180_000,
        proxyTimeout: 180_000,
      },
    },
  },
});
