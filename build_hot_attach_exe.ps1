$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    & "$projectRoot\make_icon.ps1"
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --noupx `
        --collect-all capstone `
        --noconsole `
        --icon "$projectRoot\assets\gamma22.ico" `
        --add-data "$projectRoot\assets\gamma22.ico;assets" `
        --add-data "$projectRoot\assets\gamma22-disabled.ico;assets" `
        --name Gamma22Tray `
        tray_gamma22.py

    $archivePath = Join-Path $projectRoot 'dist\BrowserGammaFix-win64.zip'
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Compress-Archive `
        -LiteralPath (Join-Path $projectRoot 'dist\Gamma22Tray') `
        -DestinationPath $archivePath `
        -CompressionLevel Optimal
    Write-Host "Created $archivePath"
}
finally {
    Pop-Location
}
