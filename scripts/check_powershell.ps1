# Syntaxpruefung der PowerShell-Skripte (Agent-Installation, CI-Job "agent-scripts").
# Parst jede Datei mit dem PowerShell-Parser - Tippfehler, fehlende Klammern oder falsche
# Backtick-Fortsetzungen fallen so schon in der CI auf, nicht erst auf der Windows-Station.
param([string[]]$Paths = @("deploy/collector-agent"))
$ErrorActionPreference = "Stop"
$failed = 0
$files = Get-ChildItem -Path $Paths -Recurse -Include *.ps1, *.psm1
foreach ($file in $files) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        $failed++
        foreach ($e in $errors) {
            Write-Host ("{0}:{1}:{2}: {3}" -f $file.FullName, $e.Extent.StartLineNumber, $e.Extent.StartColumnNumber, $e.Message)
        }
    }
    else {
        Write-Host "OK  $($file.Name)"
    }
}
if ($files.Count -eq 0) { Write-Host "keine PowerShell-Skripte gefunden"; exit 1 }
if ($failed -gt 0) { Write-Host "$failed Datei(en) mit Syntaxfehlern"; exit 1 }
