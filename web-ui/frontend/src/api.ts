// Schlanker API-Client. Das UI spricht ausschliesslich mit dem Orchestrator
// (ARCHITECTURE.md) - im Dev ueber den Vite-Proxy unter /api.

export interface HealthStatus {
  status: string;
  db: string;
}

export interface Rule {
  // Platzhalter - das Regelmodell wird in M5 definiert
  id: string;
  name: string;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new Error(`health: HTTP ${response.status}`);
  return response.json();
}

export async function fetchRules(): Promise<Rule[]> {
  const response = await fetch("/api/rules");
  if (!response.ok) throw new Error(`rules: HTTP ${response.status}`);
  return response.json();
}
