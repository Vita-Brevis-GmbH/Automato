# ADR 0001: Monorepo als Startstruktur

## Status
Angenommen

## Kontext
Die Plattform besteht aus mehreren strikt getrennten Microservices (Orchestrator, Worker je
Plattform, Ansible, Source of Truth, Documentation Engine, Web UI, Integrations, Security).
Das Projekt startet klein und soll schrittweise wachsen.

## Entscheidung
Start als **Monorepo** mit klar getrennten Top-Level-Ordnern pro Komponente statt sofortiger
Aufteilung in einzelne Repositories.

## Begründung
- Einfacheres gemeinsames Setup/CI in der Startphase (ein Team, ein Repo)
- Ordnerstruktur erzwingt bereits jetzt die spätere Trennung – Umzug zu Multi-Repo ist ohne
  Architekturänderung möglich, sobald Teamgröße/Deployment-Frequenz das rechtfertigen
- Vermeidet Overhead (Versionsabstimmung zwischen Repos) in der Phase, in der sich die
  Schnittstellen zwischen den Komponenten noch häufig ändern

## Konsequenzen
- Bei wachsendem Team ggf. Re-Evaluation nötig, insbesondere sobald einzelne Worker eigene
  Release-Zyklen benötigen
- CI muss so gebaut werden, dass sie pro Ordner/Komponente selektiv baut/testet (kein
  monolithischer Build)
