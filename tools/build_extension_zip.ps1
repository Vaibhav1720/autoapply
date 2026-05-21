# Bundle the Chrome extension into a zip served by the Flutter web app.
# Run this whenever extension/ is modified, then re-deploy the Flutter web build.
#
# Usage:  pwsh tools/build_extension_zip.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $repo 'extension'
$dest = Join-Path $repo 'app/web/autoapply-extension.zip'

if (!(Test-Path $src)) { throw "Extension source folder not found: $src" }

if (Test-Path $dest) { Remove-Item $dest -Force }
Compress-Archive -Path "$src/*" -DestinationPath $dest -Force

$size = (Get-Item $dest).Length
Write-Host "Built $dest ($size bytes)"
