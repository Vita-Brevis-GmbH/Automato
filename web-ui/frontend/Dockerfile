# Oberfläche (1.0.0, Roadmap Version 1 Nr. 6): Produktions-Build – statische Dateien hinter Caddy,
# /api an den Orchestrator. Vorgabe ist das Ziel "prod"; der Vite-Server (bis 0.141.x im Betrieb)
# bleibt als Ziel "dev" (AUTOMATO_UI_TARGET=dev in .env, z. B. im Labor).

# --- Bauen: statische Dateien (dist/) ---
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# --- Entwicklung: Vite-Server mit Proxy (vite.config.ts) ---
FROM node:22-slim AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

# --- Betrieb: Caddy liefert dist/ aus und leitet /api an den Orchestrator weiter ---
FROM caddy:2-alpine AS prod
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/tls-*.caddy deploy/api-*.caddy /etc/caddy/
COPY --from=build /app/dist /srv
EXPOSE 5173
# Lebenszeichen: Startseite auf 5173 (mit TLS intern per HTTPS, Zertifikat der internen CA)
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD if [ "$AUTOMATO_INTERNAL_TLS" = on ]; then u=https; else u=http; fi; \
      wget -q -O /dev/null --no-check-certificate "$u://127.0.0.1:5173/healthz" || exit 1
