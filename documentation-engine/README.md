# Documentation Engine

Erfasst den Live-Zustand der Zielsysteme, generiert daraus automatisch Dokumentation und
vergleicht ihn mit dem Soll-Zustand aus NetBox (Compliance-/Drift-Reports).

## Prinzip
- Agiert **rein lesend** gegenüber Zielsystemen (keine Schreibrechte)
- Nutzt Ansible Facts / Vendor-APIs zur Live-State-Erfassung
- Generiert versionierte Doku (geplant: MkDocs Material)
- Schreibt Compliance-Reports zurück in Richtung Source of Truth / Web UI

Status: 🚧 noch nicht implementiert (siehe ROADMAP.md, Phase 1–2)
