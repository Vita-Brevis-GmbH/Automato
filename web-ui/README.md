# Web UI

Bedienoberfläche der Plattform inkl. Wizards. Spricht ausschließlich mit dem Orchestrator –
nie direkt mit Workern, Ansible oder Zielsystemen.

## Stack
React + Vite + TypeScript (`frontend/`, Entscheid: ADR 0003). Im Dev läuft der
Zugriff auf den Orchestrator über den Vite-Proxy (`/api/*`); ein eigenes BFF
(Auth-Proxy) folgt mit dem UI-Ausbau.

Status: 🏗️ Skeleton vorhanden (Health-Anzeige, Regel-Tabellen-Platzhalter für
UC1-Filter). Ausbau gemäss UC1 nach M5; Wizards siehe ROADMAP-Ausblick.

Lokal: `cd frontend && npm install && npm run dev`
