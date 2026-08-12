[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ApkPath,
    [string]$PreviousApkPath,
    [string]$ExpectedPackage = 'com.travelplanet.app',
    [int]$ExpectedVersionCode = 0,
    [string]$ExpectedVersionName,
    [string]$CloudHost = '114.132.201.196:8000'
)

$ErrorActionPreference = 'Stop'

function Resolve-AndroidSdk {
    $candidates = @(
        @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA 'Android\Sdk')) |
            Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
    )
    if (-not $candidates) { throw 'Android SDK not found. Set ANDROID_HOME.' }
    return (Resolve-Path -LiteralPath $candidates[0]).Path
}

function Get-BuildTool {
    param([string]$SdkRoot, [string]$Name)
    $directory = Get-ChildItem -LiteralPath (Join-Path $SdkRoot 'build-tools') -Directory |
        Sort-Object { [version]$_.Name } -Descending |
        Select-Object -First 1
    if (-not $directory) { throw 'Android Build Tools not found.' }
    $tool = Join-Path $directory.FullName $Name
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Missing Android tool: $tool" }
    return $tool
}

function Invoke-NativeCapture {
    param([string]$FilePath, [string[]]$Arguments)
    $output = & $FilePath @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "$FilePath failed with exit code $LASTEXITCODE`n$($output -join "`n")" }
    return ($output -join "`n")
}

function Get-SignerDigest {
    param([string]$SignerTool, [string]$InputApk)
    $text = Invoke-NativeCapture -FilePath $SignerTool -Arguments @('verify', '--verbose', '--print-certs', $InputApk)
    $match = [regex]::Match($text, 'Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]+)')
    if (-not $match.Success) { throw 'Could not read APK signer SHA-256 digest.' }
    return [PSCustomObject]@{ Text = $text; Digest = $match.Groups[1].Value.ToLowerInvariant() }
}

$resolvedApk = (Resolve-Path -LiteralPath $ApkPath).Path
$sdkRoot = Resolve-AndroidSdk
$aapt = Get-BuildTool -SdkRoot $sdkRoot -Name 'aapt.exe'
$apksigner = Get-BuildTool -SdkRoot $sdkRoot -Name 'apksigner.bat'
$zipalign = Get-BuildTool -SdkRoot $sdkRoot -Name 'zipalign.exe'
$tempApk = Join-Path ([System.IO.Path]::GetTempPath()) ("travelplanet-check-{0}.apk" -f [guid]::NewGuid().ToString('N'))

Copy-Item -LiteralPath $resolvedApk -Destination $tempApk -Force
try {
    $badging = Invoke-NativeCapture -FilePath $aapt -Arguments @('dump', 'badging', $tempApk)
    $packageMatch = [regex]::Match($badging, "package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'")
    if (-not $packageMatch.Success) { throw 'Could not parse APK package metadata.' }
    $packageName = $packageMatch.Groups[1].Value
    $versionCode = [int]$packageMatch.Groups[2].Value
    $versionName = $packageMatch.Groups[3].Value
    if ($packageName -ne $ExpectedPackage) { throw "Unexpected package ID: $packageName" }
    if ($ExpectedVersionCode -gt 0 -and $versionCode -ne $ExpectedVersionCode) { throw "Expected versionCode $ExpectedVersionCode, got $versionCode" }
    if ($ExpectedVersionName -and $versionName -ne $ExpectedVersionName) { throw "Expected versionName $ExpectedVersionName, got $versionName" }

    $permissions = Invoke-NativeCapture -FilePath $aapt -Arguments @('dump', 'permissions', $tempApk)
    if ($permissions -notmatch 'android\.permission\.INTERNET') { throw 'APK is missing INTERNET permission.' }

    $signer = Get-SignerDigest -SignerTool $apksigner -InputApk $tempApk
    if ($signer.Text -notmatch 'Verified using v2 scheme \(APK Signature Scheme v2\): true') { throw 'APK Signature Scheme v2 verification failed.' }
    [void](Invoke-NativeCapture -FilePath $zipalign -Arguments @('-c', '-v', '4', $tempApk))

    if ($PreviousApkPath) {
        $resolvedPrevious = (Resolve-Path -LiteralPath $PreviousApkPath).Path
        $previousTemp = Join-Path ([System.IO.Path]::GetTempPath()) ("travelplanet-previous-{0}.apk" -f [guid]::NewGuid().ToString('N'))
        Copy-Item -LiteralPath $resolvedPrevious -Destination $previousTemp -Force
        try {
            $previousBadging = Invoke-NativeCapture -FilePath $aapt -Arguments @('dump', 'badging', $previousTemp)
            $previousPackageMatch = [regex]::Match($previousBadging, "package: name='([^']+)' versionCode='([^']+)'")
            if (-not $previousPackageMatch.Success) { throw 'Could not parse previous APK package metadata.' }
            if ($previousPackageMatch.Groups[1].Value -ne $ExpectedPackage) { throw 'Previous APK has a different package ID.' }
            $previousVersionCode = [int]$previousPackageMatch.Groups[2].Value
            if ($versionCode -le $previousVersionCode) {
                throw "versionCode $versionCode is not greater than previous APK versionCode $previousVersionCode; direct update would fail."
            }
            $previousSigner = Get-SignerDigest -SignerTool $apksigner -InputApk $previousTemp
        }
        finally { Remove-Item -LiteralPath $previousTemp -Force -ErrorAction SilentlyContinue }
        if ($previousSigner.Digest -ne $signer.Digest) { throw 'Signing certificate differs from the previous APK; direct update would fail.' }
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($tempApk)
    try {
        $configEntry = $zip.GetEntry('assets/capacitor.config.json')
        if (-not $configEntry) { throw 'Embedded Capacitor config is missing.' }
        $reader = [System.IO.StreamReader]::new($configEntry.Open())
        try { $configText = $reader.ReadToEnd() } finally { $reader.Dispose() }
        $config = $configText | ConvertFrom-Json
        if ($config.server.androidScheme -ne 'http' -or $config.server.cleartext -ne $true) { throw 'Embedded Capacitor HTTP server settings are incorrect.' }
        if ($config.android.allowMixedContent -ne $true) { throw 'Embedded allowMixedContent is not true.' }
        if ($config.plugins.CapacitorHttp.enabled -ne $true) { throw 'Embedded CapacitorHttp is not enabled.' }

        $cloudOccurrences = 0
        $localApiOccurrences = 0
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '\.(js|json|html|css)$') {
                $entryReader = [System.IO.StreamReader]::new($entry.Open())
                try { $text = $entryReader.ReadToEnd() } finally { $entryReader.Dispose() }
                $cloudOccurrences += ([regex]::Matches($text, [regex]::Escape($CloudHost))).Count
                $localApiOccurrences += ([regex]::Matches($text, '(localhost|127\.0\.0\.1):8000')).Count
            }
        }
        if ($cloudOccurrences -lt 2) { throw "Cloud host $CloudHost is not embedded in both API and asset configuration." }
        if ($localApiOccurrences -gt 0) { throw 'A localhost:8000 API or asset endpoint is embedded in the APK.' }
    }
    finally { $zip.Dispose() }

    [PSCustomObject]@{
        Apk = $resolvedApk
        Package = $packageName
        VersionCode = $versionCode
        VersionName = $versionName
        SignerSHA256 = $signer.Digest
        SignatureV2 = $true
        ZipAligned = $true
        AndroidScheme = 'http'
        CloudHostOccurrences = $cloudOccurrences
        LocalApiOccurrences = $localApiOccurrences
        SHA256 = (Get-FileHash -LiteralPath $resolvedApk -Algorithm SHA256).Hash
    }
}
finally {
    Remove-Item -LiteralPath $tempApk -Force -ErrorAction SilentlyContinue
}
