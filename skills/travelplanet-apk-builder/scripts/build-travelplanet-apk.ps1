[CmdletBinding()]
param(
    [string]$RepoRoot = (Get-Location).Path,
    [int]$VersionCode = 0,
    [string]$VersionName,
    [string]$OutputName = ("TravelPlanet-{0}.apk" -f (Get-Date -Format 'yyyyMMdd')),
    [switch]$KeepGradleOutputs
)

$ErrorActionPreference = 'Stop'
$cloudApi = 'http://114.132.201.196:8000/api'
$cloudAssets = 'http://114.132.201.196:8000'
$expectedIconHash = 'A013D537F49C1CCFE638E57B6069ECBEF1B728478EEE51963608A8A7C5F70A54'

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory)
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE" }
    }
    finally { Pop-Location }
}

$root = (Resolve-Path -LiteralPath $RepoRoot).Path
if ((Split-Path $root -Leaf) -eq '前端') { $root = Split-Path $root -Parent }
$frontend = Join-Path $root '前端'
$android = Join-Path $frontend 'android'
$configPath = Join-Path $frontend 'capacitor.config.json'
$nextConfigPath = Join-Path $frontend 'next.config.mjs'
$gradlePath = Join-Path $android 'app\build.gradle'
$iconPath = Join-Path $frontend 'design\travelplanet-app-icon.png'
$skillRoot = Split-Path $PSScriptRoot -Parent
$referenceIcon = Join-Path $skillRoot 'assets\travelplanet-app-icon.png'
$referenceAndroidIcons = Join-Path $skillRoot 'assets\android-res'
$verifier = Join-Path $PSScriptRoot 'verify-travelplanet-apk.ps1'

foreach ($required in @($frontend, $android, $configPath, $nextConfigPath, $gradlePath, $iconPath, $referenceIcon, $referenceAndroidIcons, $verifier)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path is missing: $required" }
}

$repoIconHash = (Get-FileHash -LiteralPath $iconPath -Algorithm SHA256).Hash
$referenceIconHash = (Get-FileHash -LiteralPath $referenceIcon -Algorithm SHA256).Hash
if ($repoIconHash -ne $expectedIconHash -or $referenceIconHash -ne $expectedIconHash) {
    throw 'The permanent TravelPlanet icon differs from the approved image. Stop and ask before replacing it.'
}

$nextConfig = Get-Content -LiteralPath $nextConfigPath -Raw
if ($nextConfig -notmatch '(?m)^\s*output\s*:\s*["'']export["'']') { throw 'next.config.mjs must contain output: "export" before packaging.' }
if ($nextConfig -notmatch 'unoptimized\s*:\s*true') { throw 'next.config.mjs must retain images.unoptimized: true.' }

$capacitor = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($capacitor.appId -ne 'com.travelplanet.app') { throw 'Capacitor appId must remain com.travelplanet.app.' }
if ($capacitor.server.androidScheme -ne 'http' -or $capacitor.server.cleartext -ne $true) { throw 'Capacitor server must use androidScheme=http and cleartext=true.' }
if ($capacitor.android.allowMixedContent -ne $true) { throw 'Capacitor android.allowMixedContent must be true.' }
if ($capacitor.plugins.CapacitorHttp.enabled -ne $true) { throw 'CapacitorHttp must be enabled.' }

$gradleOriginal = [System.IO.File]::ReadAllText($gradlePath)
$codeMatch = [regex]::Match($gradleOriginal, '(?m)^\s*versionCode\s+(\d+)\s*$')
$nameMatch = [regex]::Match($gradleOriginal, '(?m)^\s*versionName\s+["'']([^"'']+)["'']\s*$')
if (-not $codeMatch.Success -or -not $nameMatch.Success) { throw 'Could not parse Android versionCode/versionName.' }
$currentCode = [int]$codeMatch.Groups[1].Value
$currentName = $nameMatch.Groups[1].Value
if ($VersionCode -le 0) { $VersionCode = $currentCode + 1 }
if ($VersionCode -le $currentCode) { throw "versionCode must be greater than current value $currentCode." }
if (-not $VersionName) {
    $parts = $currentName.Split('.')
    if ($parts.Count -lt 2 -or $parts[-1] -notmatch '^\d+$') { throw 'Pass -VersionName because the current version name cannot be incremented safely.' }
    $parts[-1] = ([int]$parts[-1] + 1).ToString()
    $VersionName = $parts -join '.'
}

$previousApk = Get-ChildItem -LiteralPath $root -File -Filter '*.apk' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$outputPath = Join-Path $root $OutputName
$sourceApk = Join-Path $android 'app\build\outputs\apk\debug\app-debug.apk'
$delivered = $false

try {
    $updatedGradle = [regex]::Replace($gradleOriginal, '(?m)^(\s*)versionCode\s+\d+\s*$', "`${1}versionCode $VersionCode", 1)
    $updatedGradle = [regex]::Replace($updatedGradle, '(?m)^(\s*)versionName\s+["''][^"'']+["'']\s*$', "`${1}versionName `"$VersionName`"", 1)
    [System.IO.File]::WriteAllText($gradlePath, $updatedGradle, [System.Text.UTF8Encoding]::new($false))

    $env:NEXT_PUBLIC_API_BASE_URL = $cloudApi
    $env:NEXT_PUBLIC_ASSET_BASE_URL = $cloudAssets
    $env:ANDROID_HOME = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
    if (-not $env:JAVA_HOME) {
        $bundledJdk = Join-Path $env:LOCALAPPDATA 'TravelPlanet\jdk-21'
        if (Test-Path -LiteralPath $bundledJdk) { $env:JAVA_HOME = $bundledJdk }
    }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) { Invoke-Native -FilePath $git.Source -Arguments @('status', '--short') -WorkingDirectory $root }
    $pnpm = (Get-Command pnpm -ErrorAction Stop).Source
    Invoke-Native -FilePath $pnpm -Arguments @('install', '--frozen-lockfile') -WorkingDirectory $frontend
    $buildStarted = Get-Date
    Invoke-Native -FilePath $pnpm -Arguments @('build') -WorkingDirectory $frontend
    $exportIndex = Join-Path $frontend 'out\index.html'
    if (-not (Test-Path -LiteralPath $exportIndex) -or (Get-Item -LiteralPath $exportIndex).LastWriteTime -lt $buildStarted.AddMinutes(-1)) {
        throw 'Fresh Next.js static export was not produced in out/.'
    }
    Invoke-Native -FilePath $pnpm -Arguments @('exec', 'cap', 'sync', 'android') -WorkingDirectory $frontend

    $resRoot = Join-Path $android 'app\src\main\res'
    Get-ChildItem -LiteralPath $referenceAndroidIcons -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($referenceAndroidIcons.Length + 1)
        $destination = Join-Path $resRoot $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
    }

    $gradlew = Join-Path $android 'gradlew.bat'
    Invoke-Native -FilePath $gradlew -Arguments @('--no-daemon', 'assembleDebug') -WorkingDirectory $android
    if (-not (Test-Path -LiteralPath $sourceApk)) { throw 'Gradle completed without producing app-debug.apk.' }

    $verifyArgs = @{
        ApkPath = $sourceApk
        ExpectedVersionCode = $VersionCode
        ExpectedVersionName = $VersionName
    }
    if ($previousApk) { $verifyArgs.PreviousApkPath = $previousApk.FullName }
    $verification = & $verifier @verifyArgs

    Copy-Item -LiteralPath $sourceApk -Destination $outputPath -Force
    $sourceHash = (Get-FileHash -LiteralPath $sourceApk -Algorithm SHA256).Hash
    $outputHash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash
    if ($sourceHash -ne $outputHash) { throw 'Copied APK hash does not match the verified source APK.' }
    $delivered = $true

    if (-not $KeepGradleOutputs) { Invoke-Native -FilePath $gradlew -Arguments @('--no-daemon', 'clean') -WorkingDirectory $android }

    [PSCustomObject]@{
        Apk = $outputPath
        Package = $verification.Package
        VersionCode = $VersionCode
        VersionName = $VersionName
        SHA256 = $outputHash
        SignerSHA256 = $verification.SignerSHA256
        CloudHostOccurrences = $verification.CloudHostOccurrences
        GradleOutputsCleaned = (-not $KeepGradleOutputs)
    }
}
catch {
    if (-not $delivered) {
        [System.IO.File]::WriteAllText($gradlePath, $gradleOriginal, [System.Text.UTF8Encoding]::new($false))
    }
    throw
}
