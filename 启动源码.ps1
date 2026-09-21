param([string]$Python = "python")
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$localPython = Join-Path $sourceRoot '.venv\Scripts\python.exe'
if ($Python -eq 'python' -and (Test-Path -LiteralPath $localPython)) { $Python = $localPython }
$config = Join-Path $sourceRoot 'translation_config.json'
if (-not (Test-Path -LiteralPath $config)) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'translation_config.example.json') -Destination $config
}
$ffmpeg = Join-Path $sourceRoot 'ffmpeg.exe'
if (Test-Path -LiteralPath $ffmpeg) { $env:FFMPEG_PATH = $ffmpeg }
Push-Location -LiteralPath $sourceRoot
try { & $Python (Join-Path $sourceRoot 'translator_desktop.py'); exit $LASTEXITCODE }
finally { Pop-Location }
