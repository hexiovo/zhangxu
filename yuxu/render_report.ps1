param([switch]$Jpeg)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing
$report = [Console]::In.ReadToEnd() | ConvertFrom-Json
$height = 425 + (64 * @($report.rows).Count)
$image = [Drawing.Bitmap]::new(960, $height)
$graphics = [Drawing.Graphics]::FromImage($image)
$graphics.SmoothingMode = 'AntiAlias'
$graphics.TextRenderingHint = 'AntiAliasGridFit'
$graphics.Clear([Drawing.Color]::FromArgb(242, 246, 241))
$dark = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(24, 46, 37))
$light = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(65, 90, 77))
$green = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(39, 116, 79))
$track = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(219, 232, 222))
$white = [Drawing.SolidBrush]::new([Drawing.Color]::White)
$title = [Drawing.Font]::new('Microsoft YaHei', 46, 'Bold', 'Pixel')
$regular = [Drawing.Font]::new('Microsoft YaHei', 23, 'Regular', 'Pixel')
$small = [Drawing.Font]::new('Microsoft YaHei', 18, 'Regular', 'Pixel')
$amount = [Drawing.Font]::new('Microsoft YaHei', 30, 'Bold', 'Pixel')
try {
    $graphics.DrawString('余序', $regular, $green, 50, 32)
    $graphics.DrawString($report.title, $title, $dark, 48, 72)
    $graphics.DrawString($report.period, $regular, $light, 50, 140)
    $graphics.FillRectangle($white, 48, 194, 864, 108)
    for ($index=0; $index -lt $report.metrics.Count; $index++) {
        $metric = $report.metrics[$index]
        $x = 72 + (282 * $index)
        $graphics.DrawString($metric.label, $small, $light, $x, 208)
        $graphics.DrawString($metric.value, $amount, $dark, $x, 244)
    }
    $graphics.DrawString($report.caption, $small, $light, 50, 321)
    $y = 379
    foreach ($row in $report.rows) {
        $graphics.DrawString($row.label, $regular, $dark, 50, $y)
        $graphics.DrawString($row.value, $small, $light, 617, ($y + 5))
        $graphics.FillRectangle($track, 52, ($y + 38), 856, 5)
        if ($row.fraction -gt 0) {
            $graphics.FillRectangle($green, 52, ($y + 38), [single](856 * $row.fraction), 5)
        }
        $y += 64
    }
    $stream = [IO.MemoryStream]::new()
    try {
        $format = if ($Jpeg) { [Drawing.Imaging.ImageFormat]::Jpeg } else { [Drawing.Imaging.ImageFormat]::Png }
        $image.Save($stream, $format)
        [Console]::Write([Convert]::ToBase64String($stream.ToArray()))
    } finally { $stream.Dispose() }
} finally {
    foreach ($resource in @($dark, $light, $green, $track, $white, $title, $regular, $small, $amount, $graphics, $image)) {
        $resource.Dispose()
    }
}
