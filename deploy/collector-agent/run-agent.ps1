# Automato Collector-Agent - Wrapper fuer die Aufgabenplanung (ADR 0008).
# Laedt agent.env in die Prozess-Umgebung und startet den Collector im Pull-Modus.
#   .\run-agent.ps1                 -> naechsten offenen externen Lauf holen und ausfuehren
#   .\run-agent.ps1 -Check          -> nur Erreichbarkeit/Login am Zielsystem pruefen (read-only)
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
if ($Check) {
    if (-not $Platform) { $Platform = "fortigate" }
    $arguments = @("--check", "--platform", $Platform)
}
else {
    $arguments = @("--poll")
    if ($Platform) { $arguments += @("--platform", $Platform) }
}

& $exe @arguments
exit $LASTEXITCODE
