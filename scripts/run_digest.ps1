# Wrapper for Windows Task Scheduler (and ad-hoc PowerShell use). Resolves the
# project root from this script's location, sources .env, prefers the project
# venv if present, and forwards args to `python -m src.main`.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_digest.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_digest.ps1 -Push

param(
    [switch]$Push,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*(#|$)') { continue }
        $kv = $line -split '=', 2
        if ($kv.Length -eq 2) {
            $name = $kv[0].Trim()
            $value = $kv[1].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$ExtraArgs = @()
if ($Push)   { $ExtraArgs += "--push" }
if ($DryRun) { $ExtraArgs += "--dry-run" }

& $Python -m src.main @ExtraArgs
exit $LASTEXITCODE
