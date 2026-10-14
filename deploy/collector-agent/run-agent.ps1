# Automato Collector-Agent - Wrapper fuer die Aufgabenplanung (ADR 0008).
# Laedt agent.env in die Prozess-Umgebung und startet den Collector im Pull-Modus.
param(
    [string]$EnvFile = "$PSScriptRoot\agent.env",
    [string]$Platform = ""
)
$ErrorActionPreference = "Stop"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -and -not $_.TrimStart().StartsWith("#") } | ForEach-Object {
        $k, $v = $_ -split "=", 2
        if ($k -and $v) { [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process") }
    }
}

$exe = Join-Path $PSScriptRoot "venv\Scripts\automato-collector.exe"
$arguments = @("--poll")
if ($Platform) { $arguments += @("--platform", $Platform) }

& $exe @arguments
exit $LASTEXITCODE
