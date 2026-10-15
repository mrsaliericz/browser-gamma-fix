param(
    [string]$Source = "$PSScriptRoot\assets\gamma22-tray-source.png",
    [string]$Output = "$PSScriptRoot\assets\gamma22.ico",
    [string]$DisabledOutput = "$PSScriptRoot\assets\gamma22-disabled.ico",
    [string]$HdrActiveOutput = "$PSScriptRoot\assets\gamma22-hdr-active.ico",
    [string]$SdrActiveOutput = "$PSScriptRoot\assets\gamma22-sdr-active.ico",
    [string]$HdrErrorOutput = "$PSScriptRoot\assets\gamma22-hdr-error.ico",
    [string]$SdrErrorOutput = "$PSScriptRoot\assets\gamma22-sdr-error.ico",
    [string]$HdrDisabledOutput = "$PSScriptRoot\assets\gamma22-hdr-disabled.ico",
    [string]$SdrDisabledOutput = "$PSScriptRoot\assets\gamma22-sdr-disabled.ico"
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$sizes = @(16, 20, 24, 32, 48, 64, 128, 256)
$sourceBitmap = [System.Drawing.Bitmap]::FromFile($Source)

function New-MultiSizeIcon {
    param(
        [string]$Destination,
        [string]$FramePrefix,
        [bool]$Grayscale,
        [bool]$HdrSparkle = $false,
        [string]$Status = 'none'
    )

    $pngFrames = [System.Collections.Generic.List[byte[]]]::new()
    $sizeDirectory = Join-Path (Split-Path -Parent $Destination) 'icon-sizes'
    [System.IO.Directory]::CreateDirectory($sizeDirectory) | Out-Null

    $imageAttributes = $null
    if ($Grayscale) {
        $matrix = [System.Drawing.Imaging.ColorMatrix]::new()
        $matrix.Matrix00 = 0.299
        $matrix.Matrix01 = 0.299
        $matrix.Matrix02 = 0.299
        $matrix.Matrix10 = 0.587
        $matrix.Matrix11 = 0.587
        $matrix.Matrix12 = 0.587
        $matrix.Matrix20 = 0.114
        $matrix.Matrix21 = 0.114
        $matrix.Matrix22 = 0.114
        $imageAttributes = [System.Drawing.Imaging.ImageAttributes]::new()
        $imageAttributes.SetColorMatrix($matrix)
    }

    try {
        foreach ($size in $sizes) {
            $bitmap = [System.Drawing.Bitmap]::new(
                $size,
                $size,
                [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
            )
            try {
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                try {
                    $graphics.Clear([System.Drawing.Color]::Transparent)
                    $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
                    $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
                    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                    $destinationRectangle = [System.Drawing.Rectangle]::new(0, 0, $size, $size)
                    if ($imageAttributes) {
                        $graphics.DrawImage(
                            $sourceBitmap,
                            $destinationRectangle,
                            0,
                            0,
                            $sourceBitmap.Width,
                            $sourceBitmap.Height,
                            [System.Drawing.GraphicsUnit]::Pixel,
                            $imageAttributes
                        )
                    }
                    else {
                        $graphics.DrawImage(
                            $sourceBitmap,
                            $destinationRectangle,
                            0,
                            0,
                            $sourceBitmap.Width,
                            $sourceBitmap.Height,
                            [System.Drawing.GraphicsUnit]::Pixel
                        )
                    }

                    # HDR is a small gold sparkle in the upper-right corner.
                    # The lower-right dot is independent Browser Gamma Fix health:
                    # green = healthy/enabled, red = unsupported or error, gray = off.
                    if ($HdrSparkle) {
                        $sparklePen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(255, 244, 174), [Math]::Max(1, $size / 24))
                        try {
                            $cx = [int]($size * 0.80); $cy = [int]($size * 0.18); $radius = [int][Math]::Max(2, $size * 0.09)
                            $graphics.DrawLine($sparklePen, $cx - $radius, $cy, $cx + $radius, $cy)
                            $graphics.DrawLine($sparklePen, $cx, $cy - $radius, $cx, $cy + $radius)
                            $graphics.DrawLine($sparklePen, $cx - $radius + 1, $cy - $radius + 1, $cx + $radius - 1, $cy + $radius - 1)
                            $graphics.DrawLine($sparklePen, $cx + $radius - 1, $cy - $radius + 1, $cx - $radius + 1, $cy + $radius - 1)
                        }
                        finally { $sparklePen.Dispose() }
                    }
                    if ($Status -ne 'none') {
                        $color = switch ($Status) {
                            'healthy' { [System.Drawing.Color]::FromArgb(60, 205, 112) }
                            'error' { [System.Drawing.Color]::FromArgb(239, 71, 76) }
                            default { [System.Drawing.Color]::FromArgb(142, 151, 164) }
                        }
                        $diameter = [int][Math]::Max(5, $size * 0.29)
                        $coordinate = [int]($size * 0.70)
                        $dotBrush = [System.Drawing.SolidBrush]::new($color)
                        $dotOutline = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(19, 23, 29), [Math]::Max(1, $size / 32))
                        try {
                            $graphics.FillEllipse($dotBrush, $coordinate, $coordinate, $diameter, $diameter)
                            $graphics.DrawEllipse($dotOutline, $coordinate, $coordinate, $diameter, $diameter)
                        }
                        finally { $dotBrush.Dispose(); $dotOutline.Dispose() }
                    }
                }
                finally {
                    $graphics.Dispose()
                }

                $pngPath = Join-Path $sizeDirectory "$FramePrefix-$size.png"
                $bitmap.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)
                $memory = [System.IO.MemoryStream]::new()
                try {
                    $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
                    $pngFrames.Add($memory.ToArray())
                }
                finally {
                    $memory.Dispose()
                }
            }
            finally {
                $bitmap.Dispose()
            }
        }

        $outputDirectory = Split-Path -Parent $Destination
        [System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
        $file = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create)
        $writer = [System.IO.BinaryWriter]::new($file)
        try {
            $writer.Write([uint16]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]$sizes.Count)
            $offset = 6 + (16 * $sizes.Count)
            for ($index = 0; $index -lt $sizes.Count; $index++) {
                $size = $sizes[$index]
                $writer.Write([byte]($(if ($size -eq 256) { 0 } else { $size })))
                $writer.Write([byte]($(if ($size -eq 256) { 0 } else { $size })))
                $writer.Write([byte]0)
                $writer.Write([byte]0)
                $writer.Write([uint16]1)
                $writer.Write([uint16]32)
                $writer.Write([uint32]$pngFrames[$index].Length)
                $writer.Write([uint32]$offset)
                $offset += $pngFrames[$index].Length
            }
            foreach ($frame in $pngFrames) {
                $writer.Write($frame)
            }
        }
        finally {
            $writer.Dispose()
            $file.Dispose()
        }
    }
    finally {
        if ($imageAttributes) {
            $imageAttributes.Dispose()
        }
    }

    Write-Host "Created $Destination with sizes: $($sizes -join ', ')"
}

try {
    # Legacy names remain for executable and old package compatibility.
    New-MultiSizeIcon -Destination $Output -FramePrefix 'gamma22' -Grayscale $false
    New-MultiSizeIcon -Destination $DisabledOutput -FramePrefix 'gamma22-disabled' -Grayscale $true
    New-MultiSizeIcon -Destination $HdrActiveOutput -FramePrefix 'gamma22-hdr-active' -Grayscale $false -HdrSparkle $true -Status 'healthy'
    New-MultiSizeIcon -Destination $SdrActiveOutput -FramePrefix 'gamma22-sdr-active' -Grayscale $false -Status 'healthy'
    New-MultiSizeIcon -Destination $HdrErrorOutput -FramePrefix 'gamma22-hdr-error' -Grayscale $false -HdrSparkle $true -Status 'error'
    New-MultiSizeIcon -Destination $SdrErrorOutput -FramePrefix 'gamma22-sdr-error' -Grayscale $false -Status 'error'
    New-MultiSizeIcon -Destination $HdrDisabledOutput -FramePrefix 'gamma22-hdr-disabled' -Grayscale $true -HdrSparkle $true -Status 'disabled'
    New-MultiSizeIcon -Destination $SdrDisabledOutput -FramePrefix 'gamma22-sdr-disabled' -Grayscale $true -Status 'disabled'
}
finally {
    $sourceBitmap.Dispose()
}
