# Automato Collector-Agent - Wrapper fuer die Aufgabenplanung (ADR 0008).
# Laedt agent.env in die Prozess-Umgebung und startet den Collector im Pull-Modus.
#   .\run-agent.ps1                 -> naechsten offenen externen Lauf holen und ausfuehren
#   .\run-agent.ps1 -Check          -> alle zugewiesenen Geraete read-only pruefen (Login/Erreichbarkeit)
# Die Geraete und ihre Zugangsdaten werden im Web-UI beim Quellsystem erfasst und diesem
# Agenten zugewiesen (ADR 0009); agent.env enthaelt nur noch Orchestrator-Adresse + Agent-Token.
param(
    [string]$EnvFile = "$PSScriptRoot\agent.env",
    [string]$Platform = "",
    [switch]$Check
)
$ErrorActionPreference = "Stop"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -and -not $_.TrimStart().StartsWith("#") } | ForEach-Object {
        $k, $v = $_ -split "=", 2
        if ($k -and $v) { [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process") }
    }
}

$exe = Join-Path $PSScriptRoot "venv\Scripts\automato-collector.exe"
if ($Check) { $arguments = @("--check") } else { $arguments = @("--poll") }
if ($Platform) { $arguments += @("--platform", $Platform) }

& $exe @arguments
exit $LASTEXITCODE
