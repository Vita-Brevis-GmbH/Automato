// Kleine Darstellungs-Helfer (Badges) fuer Status/Aktion/Schwere.

const MATCH_CLASS: Record<string, string> = {
  matched: "b-green",
  unmatched: "b-red",
  expected_external: "b-blue",
  not_applicable: "b-grey",
  ambiguous: "b-amber",
};

const SEVERITY_CLASS: Record<string, string> = {
  error: "b-red",
  warning: "b-amber",
  info: "b-blue",
};

export function ActionBadge({ action }: { action: string }) {
  return <span className={`badge ${action === "allow" ? "b-green" : "b-red"}`}>{action}</span>;
}

export function MatchBadge({ status }: { status: string | null }) {
  if (!status) return <span className="muted">–</span>;
  return <span className={`badge ${MATCH_CLASS[status] ?? "b-grey"}`}>{status}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`badge ${SEVERITY_CLASS[severity] ?? "b-grey"}`}>{severity}</span>;
}
