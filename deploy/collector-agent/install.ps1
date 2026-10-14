# Automato Collector-Agent - Installation als geplante Aufgabe (Windows, ADR 0008).
# Installiert das collectors-Wheel in ein venv und registriert einen Task, der den Agenten
# im Pull-Modus laufen laesst. Ausfuehren in einer administrativen PowerShell.
param(
    [Parameter(Mandatory = $true)][string]$Wheel,          # Pfad zum collectors-*.whl
    [string]$InstallDir = "C:\Automato\collector",
    [int]$IntervalMinutes = 15
)
$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# venv + Wheel installieren (bringt das Konsolen-Script automato-collector mit)
$py = Join-Path $InstallDir "venv\Scripts\python.exe"
py -3 -m venv (Join-Path $InstallDir "venv")
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install $Wheel

# Wrapper + Config bereitstellen
Copy-Item "$PSScriptRoot\run-agent.ps1" $InstallDir -Force
$envFile = Join-Path $InstallDir "agent.env"
if (-not (Test-Path $envFile)) {
    Copy-Item "$PSScriptRoot\agent.env.example" $envFile
    Write-Host "Bitte $envFile mit echten Werten fuellen (Agent-Token, Ziel-Creds, TLS)."
}

# Geplante Aufgabe (SYSTEM, alle N Minuten)
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\run-agent.ps1`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited
Register-ScheduledTask -TaskName "Automato Collector Agent" `
    -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null

Write-Host "Installiert nach $InstallDir. Task 'Automato Collector Agent' laeuft alle $IntervalMinutes min."
Write-Host "Verbindung testen: & '$InstallDir\venv\Scripts\automato-collector.exe' --check --platform fortigate"
