# Automato Collector-Agent - Installation als geplante Aufgabe (Windows, ADR 0008).
# Installiert das collectors-Wheel in ein venv und registriert einen Task, der den Agenten
# im Pull-Modus laufen laesst. Ausfuehren in einer administrativen PowerShell.
# Paket aus dem Web-UI (Collector-Agenten -> Agent-Paket): ZIP entpacken, die im UI erzeugte
# agent.env daneben legen, dann:  powershell -ExecutionPolicy Bypass -File .\install.ps1
param(
    [string]$Wheel = "",                                   # Pfad zum collectors-*.whl (Default: neben dem Skript)
    [string]$InstallDir = "C:\Automato\collector",
    [int]$IntervalMinutes = 15
)
$ErrorActionPreference = "Stop"

if (-not $Wheel) {
    $found = Get-ChildItem -Path $PSScriptRoot -Filter "collectors-*.whl" | Sort-Object Name | Select-Object -Last 1
    if (-not $found) {
        Write-Error "Kein collectors-*.whl neben install.ps1 gefunden (Parameter -Wheel angeben)."
        exit 1
    }
    $Wheel = $found.FullName
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Python-Starter robust ermitteln: py -3 (python.org-Launcher), sonst python / python3.
# Nicht jede Windows-Station hat den "py"-Launcher (nur der python.org-Installer bringt ihn).
function Resolve-PythonCmd {
    foreach ($c in @(, @("py", "-3"), , @("python"), , @("python3"))) {
        if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
            $rest = @(); if ($c.Count -gt 1) { $rest = $c[1..($c.Count - 1)] }
            try {
                & $c[0] @rest --version *> $null
                if ($LASTEXITCODE -eq 0) { return , $c }
            }
            catch { }
        }
    }
    return $null
}
$pycmd = Resolve-PythonCmd
if (-not $pycmd) {
    Write-Error ("Kein Python 3 gefunden. Bitte Python 3.11+ installieren " +
        "(https://www.python.org/downloads/windows/, beim Setup 'Add python.exe to PATH' " +
        "aktivieren), eine NEUE PowerShell oeffnen und dieses Skript erneut ausfuehren.")
    exit 1
}
$pyExe = $pycmd[0]
$pyArgs = @(); if ($pycmd.Count -gt 1) { $pyArgs = $pycmd[1..($pycmd.Count - 1)] }
Write-Host ("Nutze Python: " + ($pycmd -join " "))

# venv + Wheel installieren (bringt das Konsolen-Script automato-collector mit)
$venvDir = Join-Path $InstallDir "venv"
& $pyExe @pyArgs -m venv $venvDir
$py = Join-Path $venvDir "Scripts\python.exe"
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install $Wheel
# Bei erneuter Installation (gleiche Version) den Agent-Code trotzdem aktualisieren
& $py -m pip install --force-reinstall --no-deps $Wheel | Out-Null

# Wrapper + Config bereitstellen
Copy-Item "$PSScriptRoot\run-agent.ps1" $InstallDir -Force
$envFile = Join-Path $InstallDir "agent.env"
$envNext = Join-Path $PSScriptRoot "agent.env"
if (Test-Path $envNext) {
    Copy-Item $envNext $envFile -Force                     # im Web-UI erzeugte Konfiguration
    Write-Host "agent.env uebernommen nach $envFile."
}
elseif (-not (Test-Path $envFile)) {
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
Write-Host "Verbindung testen: powershell -ExecutionPolicy Bypass -File '$InstallDir\run-agent.ps1' -Check"
