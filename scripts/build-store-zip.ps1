$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root "extension"
$stage = Join-Path $env:TEMP "autoapply-store-stage"
$zip = Join-Path $root "autoapply-extension-v1.6.15-store.zip"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
Copy-Item $src $stage -Recurse

$mfPath = Join-Path $stage "manifest.json"
$mf = Get-Content $mfPath -Raw | ConvertFrom-Json
if ($mf.PSObject.Properties.Name -contains 'key') {
  $mf.PSObject.Properties.Remove('key')
  Write-Host "Removed 'key' field from staged manifest."
} else {
  Write-Host "No 'key' field found (already clean)."
}
($mf | ConvertTo-Json -Depth 20) | Set-Content $mfPath -Encoding UTF8

if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Remove-Item $stage -Recurse -Force

$item = Get-Item $zip
Write-Host ("Built: {0} ({1} KB)" -f $item.FullName, [Math]::Round($item.Length/1KB,1))

Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($zip)
$entry = $z.Entries | Where-Object { $_.FullName -eq 'manifest.json' }
$reader = New-Object System.IO.StreamReader($entry.Open())
$contents = $reader.ReadToEnd()
$reader.Close(); $z.Dispose()
if ($contents -match '"key"\s*:') { Write-Host "FAIL: key still present" } else { Write-Host "OK: no key field in zipped manifest" }
