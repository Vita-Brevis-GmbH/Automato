import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-Proxy: /api/* geht an den Orchestrator, damit das UI ohne CORS-Konfig
// lokal und im docker-compose laeuft (Ziel via ENV, Default localhost).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.ORCHESTRATOR_URL ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
