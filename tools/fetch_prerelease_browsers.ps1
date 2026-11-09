# Install pre-release Edge and Chrome builds on a disposable GitHub Actions
# runner so the compatibility report can analyze their DLLs weeks before they
# reach Stable. The enterprise MSIs are installed silently side by side with
# Stable (Edge Beta/Dev, Chrome Beta/Dev); their installers are nested
# self-extracting packages, so installing is far more reliable than unpacking.
#
# This script refuses to run outside GitHub Actions: it installs browsers.
# Best effort: every channel is optional and failures are only reported.
param(
    [string]$Destination = (Join-Path $env:RUNNER_TEMP "prerelease-browsers")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($env:GITHUB_ACTIONS -ne "true") {
    throw "This script installs browsers and is meant only for GitHub Actions runners."
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

function Install-Msi([string]$Name, [string]$Url) {
    try {
        $package = Join-Path $Destination (($Name -replace "[^A-Za-z0-9.]", "-") + ".msi")
        Invoke-WebRequest -Uri $Url -OutFile $package -UseBasicParsing
        $size = [math]::Round((Get-Item -LiteralPath $package).Length / 1MB)
        $process = Start-Process -FilePath "msiexec.exe" -Wait -PassThru -ArgumentList @(
            "/i", "`"$package`"", "/qn", "/norestart", "/l*v", "`"$package.log`""
        )
        Write-Host "${Name}: downloaded $size MB, msiexec exit code $($process.ExitCode)"
        Remove-Item -LiteralPath $package -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "${Name}: $($_.Exception.Message)"
    }
}

try {
    $products = Invoke-RestMethod -Uri "https://edgeupdates.microsoft.com/api/products" -UseBasicParsing
    foreach ($channel in "Beta", "Dev") {
        $release = ($products | Where-Object Product -eq $channel).Releases |
            Where-Object { $_.Platform -eq "Windows" -and $_.Architecture -eq "x64" } |
            Sort-Object { [version]$_.ProductVersion } -Descending | Select-Object -First 1
        $artifact = $release.Artifacts | Where-Object ArtifactName -eq "msi" | Select-Object -First 1
        if ($artifact) {
            Install-Msi "Edge $channel $($release.ProductVersion)" $artifact.Location
        } else {
            Write-Warning "Edge ${channel}: no x64 MSI listed by the update API"
        }
    }
} catch {
    Write-Warning "Edge update API: $($_.Exception.Message)"
}

Install-Msi "Chrome Beta" "https://dl.google.com/chrome/install/beta/googlechromebetastandaloneenterprise64.msi"
Install-Msi "Chrome Dev" "https://dl.google.com/chrome/install/dev/googlechromedevstandaloneenterprise64.msi"

$patterns = @(
    "C:\Program Files*\Microsoft\Edge Beta\Application\*\msedge.dll",
    "C:\Program Files*\Microsoft\Edge Dev\Application\*\msedge.dll",
    "C:\Program Files*\Google\Chrome Beta\Application\*\chrome.dll",
    "C:\Program Files*\Google\Chrome Dev\Application\*\chrome.dll"
)
$found = @(foreach ($pattern in $patterns) {
    Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object FullName
})
foreach ($dll in $found) { Write-Host "Found $dll" }

$list = Join-Path $Destination "dlls.txt"
$found | Set-Content -LiteralPath $list -Encoding utf8
Write-Host "Found $($found.Count) pre-release DLL(s); list: $list"
