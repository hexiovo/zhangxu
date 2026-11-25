param([Parameter(Mandatory=$true)][string]$Path, [switch]$Pdf)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1
}
$withResult = $asTask | Where-Object {
    $_.IsGenericMethod -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
$action = $asTask | Where-Object {
    -not $_.IsGenericMethod -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
} | Select-Object -First 1

function Wait-WinRt($operation, [Type]$type) {
    $task = $withResult.MakeGenericMethod($type).Invoke($null, @($operation))
    return $task.GetAwaiter().GetResult()
}

[void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
$file = Wait-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'no installed OCR language' }
$lines = [Collections.Generic.List[string]]::new()

function Read-Image($stream) {
    $decoder = Wait-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $largest = [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight)
    if ($largest -gt 12000 -or ([int64]$decoder.PixelWidth * $decoder.PixelHeight) -gt 60000000) { throw 'oversized image' }
    $limit = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($largest -gt $limit) {
        $transform = [Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType=WindowsRuntime]::new()
        $transform.ScaledWidth = [uint32]([Math]::Max(1, [Math]::Floor($decoder.PixelWidth * $limit / $largest)))
        $transform.ScaledHeight = [uint32]([Math]::Max(1, [Math]::Floor($decoder.PixelHeight * $limit / $largest)))
        $operation = $decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
            $transform, [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)
    } else {
        $operation = $decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)
    }
    $bitmap = Wait-WinRt $operation ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $result = Wait-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        foreach ($line in $result.Lines) { $lines.Add($line.Text) }
    } finally { $bitmap.Dispose() }
}

if ($Pdf) {
    $document = Wait-WinRt ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
    if ($document.PageCount -gt 5) { throw 'more than five PDF pages' }
    for ($i = 0; $i -lt $document.PageCount; $i++) {
        $page = $document.GetPage($i)
        $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
        try {
            [void]$action.Invoke($null, @($page.RenderToStreamAsync($stream))).GetAwaiter().GetResult()
            $stream.Seek(0)
            Read-Image $stream
        } finally { $page.Dispose(); $stream.Dispose() }
    }
} else {
    $stream = Wait-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try { Read-Image $stream } finally { $stream.Dispose() }
}
[Console]::Write((ConvertTo-Json -InputObject ($lines.ToArray()) -Compress))
