# Build a standalone Windows bundle in dist\ytdlp-gui\ and a .zip.
# Optional:  .\packaging\build_windows.ps1 -Ffmpeg C:\ffmpeg\bin\ffmpeg.exe   (bundles ffmpeg into bin\)
param([string]$Ffmpeg = "")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
py -m pip install -e ".[dev]"
py -m PyInstaller --noconfirm --distpath dist --workpath build packaging\ytdlp-gui.spec
if ($Ffmpeg) {
    New-Item -ItemType Directory -Force dist\ytdlp-gui\bin | Out-Null
    Copy-Item $Ffmpeg dist\ytdlp-gui\bin\ffmpeg.exe
    $probe = Join-Path (Split-Path $Ffmpeg) "ffprobe.exe"
    if (Test-Path $probe) { Copy-Item $probe dist\ytdlp-gui\bin\ }
}
Compress-Archive -Force dist\ytdlp-gui dist\ytdlp-gui-windows-x64.zip
Write-Host "Built dist\ytdlp-gui\ytdlp-gui.exe and dist\ytdlp-gui-windows-x64.zip"
