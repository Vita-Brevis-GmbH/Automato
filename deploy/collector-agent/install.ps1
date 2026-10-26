# Automato Collector-Agent - Installation als geplante Aufgabe (Windows, ADR 0008).
# Installiert das collectors-Wheel in ein venv und registriert einen Task, der den Agenten
# im Pull-Modus laufen laesst. Ausfuehren in einer administrativen PowerShell.
# Paket aus dem Web-UI (Collector-Agenten -> Agent-Paket): ZIP entpacken, die im UI erzeugte
# agent.env daneben legen, dann:  powershell -ExecutionPolicy Bypass -File .\install.ps1
param(
    [string]$Wheel = "",                                   # Pfad zum collectors-*.whl (Default: neben dem Skript)
    [string]$InstallDir = "C:\Automato\collector",
    [int]$IntervalMinutes = 15,                            # Neustart der Aufgabe (inkl. Selbst-Update-Pruefung)
    [int]$WatchSeconds = 20                                # Abfrage-Intervall dazwischen (0 = nur beim Start)
)
$ErrorActionPreference = "Stop"
$TaskName = "Automato Collector Agent"

# Aus dem Internet geladene Dateien entsperren (Mark-of-the-Web) - sonst fragt Windows bei
# jedem Start von run-agent.ps1 nach ("Sicherheitswarnung").
Get-ChildItem -Path $PSScriptRoot -File | Unblock-File

# Native Befehle (pip) melden Fehler nur ueber den Exit-Code -> hier pruefen
function Invoke-Checked([string]$what, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$what fehlgeschlagen (Exit $LASTEXITCODE) - Ausgabe oben pruefen." }
}

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
    foreach ($label in @("py -3", "python", "python3")) {
        $parts = $label -split " "
        $exe = $parts[0]
        $rest = @($parts | Select-Object -Skip 1)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try {
                & $exe @rest --version *> $null
                if ($LASTEXITCODE -eq 0) { return @{ Exe = $exe; Args = $rest; Label = $label } }
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
$pyExe = $pycmd.Exe
$pyArgs = $pycmd.Args
Write-Host ("Nutze Python: " + $pycmd.Label + " (" + (& $pyExe @pyArgs --version 2>&1) + ")")

# Versionen (Paket aus dem Web-UI: agent-version.txt = Version+Build)
$newVersion = ""
if (Test-Path "$PSScriptRoot\agent-version.txt") { $newVersion = (Get-Content "$PSScriptRoot\agent-version.txt" -Raw).Trim() }
$oldVersion = ""
if (Test-Path "$InstallDir\agent-version.txt") { $oldVersion = (Get-Content "$InstallDir\agent-version.txt" -Raw).Trim() }
if (-not $oldVersion) { Write-Host "Installiere Automato-Agent $newVersion" }
elseif ($oldVersion -eq $newVersion) { Write-Host "Version $newVersion ist bereits installiert - installiere neu." }
else { Write-Host "Aktualisiere Automato-Agent: $oldVersion -> $newVersion" }

# Laufende Aufgabe anhalten, damit keine Dateien des Agenten gesperrt sind
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

# venv + Agent-Pakete: collectors- und sot-gateway-Wheel (Infoblox-/NetBox-Leser) immer frisch
# aus diesem Paket, danach fehlende Abhaengigkeiten (httpx, pydantic, ...) nachziehen.
$venvDir = Join-Path $InstallDir "venv"
Invoke-Checked "venv anlegen" { & $pyExe @pyArgs -m venv $venvDir }
$py = Join-Path $venvDir "Scripts\python.exe"
$wheelDir = Split-Path -Parent $Wheel
& $py -m pip install --disable-pip-version-check --upgrade pip | Out-Null
$local = @(Get-ChildItem -Path $wheelDir -Filter "sot_gateway-*.whl" | Sort-Object Name | Select-Object -Last 1 | ForEach-Object { $_.FullName })
if (-not $local) { throw "Kein sot_gateway-*.whl neben install.ps1 - Agent-Paket im Web-UI neu herunterladen." }
$local += @($Wheel)
Write-Host "Installiere Agent-Pakete:"; $local | ForEach-Object { Write-Host "  $(Split-Path -Leaf $_)" }
Invoke-Checked "Installation der Agent-Pakete" { & $py -m pip install --disable-pip-version-check --force-reinstall --no-deps @local }
Invoke-Checked "Installation der Abhaengigkeiten" { & $py -m pip install --disable-pip-version-check --quiet --find-links $wheelDir $Wheel }
# Pruefen, dass der Agent startet (alle Module, inkl. sot_gateway)
Invoke-Checked "Startpruefung des Agenten" { & $py -c "import collectors, collectors.run, sot_gateway; print('Agent-Pakete OK: collectors ' + collectors.__version__ + ', sot-gateway ' + sot_gateway.__version__)" }

# Zuletzt gute Pakete aufbewahren: daraus repariert sich der Agent, wenn ein Update abbricht
# (Selbst-Update und run-agent.ps1, 0.16.1)
$pkgDir = Join-Path $InstallDir "packages"
if (Test-Path $pkgDir) { Remove-Item $pkgDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
$local | ForEach-Object { Copy-Item $_ $pkgDir -Force }
Remove-Item (Join-Path $InstallDir "update.lock") -Force -ErrorAction SilentlyContinue

# Wrapper + Config bereitstellen
Copy-Item "$PSScriptRoot\run-agent.ps1" $InstallDir -Force
Unblock-File "$InstallDir\run-agent.ps1"
# Paket-Version (aus dem Web-UI-Download) -> kein unnoetiges Selbst-Update direkt danach
if (Test-Path "$PSScriptRoot\agent-version.txt") {
    Copy-Item "$PSScriptRoot\agent-version.txt" $InstallDir -Force
}
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

# Geplante Aufgabe (SYSTEM, Start alle N Minuten). Der Agent bleibt dazwischen wach und fragt
# alle $WatchSeconds s nach Laeufen; er endet 1 min vor dem naechsten Start. Laeuft er noch
# (langer Lauf), wird der naechste Start ausgelassen (IgnoreNew) - nie zwei Agenten parallel.
$forMinutes = [Math]::Max(1, $IntervalMinutes - 1)
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\run-agent.ps1`" -WatchSeconds $WatchSeconds -ForMinutes $forMinutes"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ("Automato-Agent $newVersion installiert nach $InstallDir. Task '$TaskName' startet alle " +
    "$IntervalMinutes min und fragt dazwischen alle $WatchSeconds s nach Laeufen.")
Write-Host "Kuenftige Versionen installiert der Agent selbst (Auto-Update, ADR 0011)."
Write-Host "Verbindung testen: powershell -ExecutionPolicy Bypass -File '$InstallDir\run-agent.ps1' -Check"
