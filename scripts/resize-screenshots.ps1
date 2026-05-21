Add-Type -AssemblyName System.Drawing
$dst = Join-Path (Split-Path -Parent $PSScriptRoot) "store-assets"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
$names = @{
  "1" = "01-popup-signin.png"
  "2" = "02-popup-signedin.png"
  "3" = "03-greenhouse-filled.png"
  "4" = "04-options-profile.png"
  "5" = "05-webapp-discover.png"
  "6" = "06-extra.png"
}
$tw = 1280; $th = 800
$files = Get-ChildItem "$env:USERPROFILE\Downloads" -Filter "*-Screenshot*.png" |
  Where-Object { $_.Name -match '^[1-6]-' } | Sort-Object Name
foreach ($f in $files) {
  $key = $f.Name.Substring(0,1)
  $out = Join-Path $dst $names[$key]
  $src = [System.Drawing.Image]::FromFile($f.FullName)
  $bmp = New-Object System.Drawing.Bitmap $tw, $th
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.Clear([System.Drawing.Color]::White)
  $sw = $src.Width; $sh = $src.Height
  $scale = [Math]::Min(($tw / $sw), ($th / $sh))
  $nw = [int]($sw * $scale); $nh = [int]($sh * $scale)
  $x = [int](($tw - $nw) / 2); $y = [int](($th - $nh) / 2)
  $g.DrawImage($src, $x, $y, $nw, $nh)
  $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose(); $src.Dispose()
  Write-Host ("Wrote {0}  (src {1}x{2} -> {3}x{4})" -f $names[$key], $sw, $sh, $tw, $th)
}
Write-Host ""
Write-Host "Final files:"
Get-ChildItem $dst -Filter *.png | ForEach-Object {
  $img = [System.Drawing.Image]::FromFile($_.FullName)
  Write-Host ("  {0,-30} {1}x{2}  {3} KB" -f $_.Name, $img.Width, $img.Height, [Math]::Round($_.Length/1KB,1))
  $img.Dispose()
}
