import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-Proxy: /api/* geht an den Orchestrator, damit das UI ohne CORS-Konfig
// lokal und im docker-compose laeuft (Ziel via ENV, Default localhost).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 0.0.0.0 - im Container/ueber die VM-IP erreichbar
    allowedHosts: true, // Lab: Zugriff ueber beliebigen Host (VM-IP) erlauben
    proxy: {
      "/api": {
        target: process.env.ORCHESTRATOR_URL ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
