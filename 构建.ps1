param([string]$Python = "python")
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$localPython = Join-Path $sourceRoot '.venv\Scripts\python.exe'
if ($Python -eq 'python' -and (Test-Path -LiteralPath $localPython)) { $Python = $localPython }
Push-Location -LiteralPath $sourceRoot
try {
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath build build_desktop_201.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    $output = Join-Path $sourceRoot 'dist\常客AI'
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'translation_config.example.json') -Destination (Join-Path $output 'translation_config.json')
    $ffmpeg = Join-Path $sourceRoot 'ffmpeg.exe'
    if (Test-Path -LiteralPath $ffmpeg) { Copy-Item -LiteralPath $ffmpeg -Destination $output }
    Write-Host "Build ready: $output. Distribute the entire folder, including _internal."
} finally { Pop-Location }
