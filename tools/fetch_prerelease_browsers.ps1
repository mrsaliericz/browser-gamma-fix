# Download and unpack (never install) pre-release Edge and Chrome builds so the
# compatibility report can analyze their DLLs weeks before they reach Stable.
# Best effort: every channel is optional and failures are only reported.
param(
    [string]$Destination = (Join-Path $env:RUNNER_TEMP "prerelease-browsers")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue).Source
if (-not $sevenZip) { $sevenZip = "C:\Program Files\7-Zip\7z.exe" }

function Expand-UntilFound([string]$Archive, [string]$Output, [string]$Pattern, [int]$Depth) {
    & $sevenZip x -y -bso0 -bsp0 -bse0 "-o$Output" $Archive | Out-Null
    $found = Get-ChildItem -LiteralPath $Output -Recurse -Filter $Pattern -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found -or $Depth -le 0) { return $found }
    $inner = Get-ChildItem -LiteralPath $Output -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".7z", ".exe", ".msi", ".cab" -or $_.Name -match "\.7z$" } |
        Sort-Object Length -Descending | Select-Object -First 3
    foreach ($candidate in $inner) {
        $result = Expand-UntilFound $candidate.FullName ($candidate.FullName + ".x") $Pattern ($Depth - 1)
        if ($result) { return $result }
    }
    return $null
}

function Get-Browser([string]$Name, [string]$Url, [string]$Pattern) {
    try {
        $folder = Join-Path $Destination ($Name -replace "\s", "-")
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $package = Join-Path $folder ([IO.Path]::GetFileName(([Uri]$Url).AbsolutePath))
        Invoke-WebRequest -Uri $Url -OutFile $package -UseBasicParsing
        $dll = Expand-UntilFound $package (Join-Path $folder "x") $Pattern 4
        if ($dll) {
            Write-Host "$Name -> $($dll.FullName)"
            return $dll.FullName
        }
        Write-Warning "${Name}: $Pattern not found in $Url"
    } catch {
        Write-Warning "${Name}: $($_.Exception.Message)"
    }
    return $null
}

$found = @()
try {
    $products = Invoke-RestMethod -Uri "https://edgeupdates.microsoft.com/api/products" -UseBasicParsing
    foreach ($channel in "Beta", "Dev", "Canary") {
        $release = ($products | Where-Object Product -eq $channel).Releases |
            Where-Object { $_.Platform -eq "Windows" -and $_.Architecture -eq "x64" } |
            Sort-Object { [version]$_.ProductVersion } -Descending | Select-Object -First 1
        $artifact = $release.Artifacts | Where-Object ArtifactName -in "msi", "exe" | Select-Object -First 1
        if ($artifact) {
            $dll = Get-Browser "Edge $channel $($release.ProductVersion)" $artifact.Location "msedge.dll"
            if ($dll) { $found += $dll }
        }
    }
} catch {
    Write-Warning "Edge update API: $($_.Exception.Message)"
}

foreach ($channel in @(
    @{ Name = "Chrome Beta"; Url = "https://dl.google.com/chrome/install/beta/googlechromebetastandaloneenterprise64.msi" },
    @{ Name = "Chrome Dev"; Url = "https://dl.google.com/chrome/install/dev/googlechromedevstandaloneenterprise64.msi" }
)) {
    $dll = Get-Browser $channel.Name $channel.Url "chrome.dll"
    if ($dll) { $found += $dll }
}

$list = Join-Path $Destination "dlls.txt"
$found | Set-Content -LiteralPath $list -Encoding utf8
Write-Host "Found $($found.Count) pre-release DLL(s); list: $list"
