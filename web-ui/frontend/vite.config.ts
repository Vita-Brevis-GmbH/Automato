import { readFileSync } from "node:fs";
import { Agent } from "node:https";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// TLS intern (0.49.0, ADR 0023): mit AUTOMATO_INTERNAL_TLS=on liefert die Web-UI per HTTPS aus
// (Zertifikat aus der internen CA) und spricht den Orchestrator per HTTPS an - geprueft gegen
// die interne CA (ca.crt im selben Ordner).
const tlsDir = process.env.AUTOMATO_INTERNAL_TLS === "on" ? (process.env.AUTOMATO_INTERNAL_TLS_DIR ?? "/etc/automato/tls") : null;
const target = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";

// Dev-Proxy: /api/* geht an den Orchestrator, damit das UI ohne CORS-Konfig
// lokal und im docker-compose laeuft (Ziel via ENV, Default localhost).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 0.0.0.0 - im Container/ueber die VM-IP erreichbar
    allowedHosts: true, // Lab: Zugriff ueber beliebigen Host (VM-IP) erlauben
    https: tlsDir ? { cert: readFileSync(`${tlsDir}/server.crt`), key: readFileSync(`${tlsDir}/server.key`) } : undefined,
    proxy: {
      "/api": {
        target: tlsDir ? target.replace(/^http:/, "https:") : target,
        changeOrigin: true,
        secure: true, // Zertifikat des Orchestrators pruefen (bei HTTPS)
        agent: tlsDir ? new Agent({ ca: readFileSync(`${tlsDir}/ca.crt`), keepAlive: true }) : undefined,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
