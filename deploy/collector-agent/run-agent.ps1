# Automato Collector-Agent - Wrapper fuer die Aufgabenplanung (ADR 0008).
# Laedt agent.env in die Prozess-Umgebung und startet den Collector im Pull-Modus.
#   .\run-agent.ps1                 -> naechsten offenen externen Lauf holen und ausfuehren
#   .\run-agent.ps1 -Check          -> alle zugewiesenen Geraete read-only pruefen (Login/Erreichbarkeit)
# Vor jedem Aufruf aktualisiert sich der Agent selbst, wenn der Orchestrator eine neue Version
# anbietet und das Update fuer diesen Agenten erlaubt ist (ADR 0011).
# Die Geraete und ihre Zugangsdaten werden im Web-UI beim Quellsystem erfasst und diesem
# Agenten zugewiesen (ADR 0009); agent.env enthaelt nur noch Orchestrator-Adresse + Agent-Token.
param(
    [string]$EnvFile = "$PSScriptRoot\agent.env",
    [string]$Platform = "",
    [switch]$Check,
    [switch]$NoUpdate                                    # Selbst-Update fuer diesen Aufruf auslassen
)
$ErrorActionPreference = "Stop"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -and -not $_.TrimStart().StartsWith("#") } | ForEach-Object {
        $k, $v = $_ -split "=", 2
        if ($k -and $v) { [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process") }
    }
}

# Ueber python.exe starten (nicht automato-collector.exe): so ist die .exe beim Selbst-Update
# des Agenten (ADR 0011) nicht gesperrt und kann ersetzt werden.
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if ($Check) { $arguments = @("--check") } else { $arguments = @("--poll") }
if ($Platform) { $arguments += @("--platform", $Platform) }
if ($NoUpdate) { $arguments += @("--no-update") }

& $python -m collectors.run @arguments
exit $LASTEXITCODE
