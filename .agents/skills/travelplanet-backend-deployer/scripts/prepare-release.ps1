[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [string]$OutputPath,

    [switch]$FullSnapshot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $FullSnapshot) {
    throw "This workflow packages secrets and runtime data. Re-run with -FullSnapshot after confirming that is intended."
}

$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$backend = Join-Path $repo "后端"
if (-not (Test-Path -LiteralPath $backend -PathType Container)) {
    throw "Backend directory not found: $backend"
}

$required = @(
    ".env",
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "alembic.ini",
    "app\main.py",
    "data\travelplanet.db"
)
foreach ($relative in $required) {
    $path = Join-Path $backend $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required release file is missing: $path"
    }
    if ((Get-Item -LiteralPath $path).Length -eq 0) {
        throw "Required release file is empty: $path"
    }
}

$tar = Get-Command tar.exe -ErrorAction Stop
$robocopy = Get-Command robocopy.exe -ErrorAction Stop
$skillRoot = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if (-not $OutputPath) {
    $OutputPath = Join-Path $repo "travelplanet_backend_deploy_$stamp.tar.gz"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repo $OutputPath
}
$output = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $output) {
    throw "Output already exists: $output"
}
$outputParent = Split-Path -Parent $output
if (-not (Test-Path -LiteralPath $outputParent -PathType Container)) {
    throw "Output parent does not exist: $outputParent"
}

$stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("travelplanet-backend-release-" + [guid]::NewGuid().ToString("N"))
$payload = Join-Path $stageRoot "payload"
New-Item -ItemType Directory -Path $payload -Force | Out-Null

try {
    $arguments = @(
        $backend,
        $payload,
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XD",
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "logs",
        "tests",
        "/XF",
        "*.pyc",
        "*.pyo",
        "*.db-shm",
        "*.db-wal",
        "*.tar.gz",
        "*.zip"
    )
    & $robocopy.Source @arguments | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }

    $dockerfile = Join-Path $payload "Dockerfile"
    if (-not (Test-Path -LiteralPath $dockerfile -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $skillRoot "assets\Dockerfile") -Destination $dockerfile
    }
    Copy-Item -LiteralPath (Join-Path $skillRoot "assets\dockerignore") -Destination (Join-Path $payload ".dockerignore") -Force

    $deployDir = Join-Path $payload "deploy"
    New-Item -ItemType Directory -Path $deployDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $skillRoot "assets\probe_12306_mcp.py") -Destination (Join-Path $deployDir "probe_12306_mcp.py") -Force

    & $tar.Source -czf $output -C $payload .
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }

    $entries = @(& $tar.Source -tzf $output)
    if ($LASTEXITCODE -ne 0) {
        throw "Archive listing failed with exit code $LASTEXITCODE"
    }
    $checks = @{
        Env = [bool]($entries | Where-Object { $_ -match '(^|/)\.env$' })
        Database = [bool]($entries | Where-Object { $_ -match '(^|/)data/travelplanet\.db$' })
        Dockerfile = [bool]($entries | Where-Object { $_ -match '(^|/)Dockerfile$' })
        Dockerignore = [bool]($entries | Where-Object { $_ -match '(^|/)\.dockerignore$' })
        McpProbe = [bool]($entries | Where-Object { $_ -match '(^|/)deploy/probe_12306_mcp\.py$' })
    }
    $missing = @($checks.GetEnumerator() | Where-Object { -not $_.Value } | ForEach-Object { $_.Key })
    if ($missing.Count -gt 0) {
        throw "Archive verification failed; missing: $($missing -join ', ')"
    }

    $staticRoot = Join-Path $backend "static"
    $staticCount = if (Test-Path -LiteralPath $staticRoot) {
        @(Get-ChildItem -LiteralPath $staticRoot -Recurse -File).Count
    } else {
        0
    }
    $archiveItem = Get-Item -LiteralPath $output
    $databaseItem = Get-Item -LiteralPath (Join-Path $backend "data\travelplanet.db")
    [pscustomobject]@{
        Archive = $archiveItem.FullName
        SHA256 = (Get-FileHash -LiteralPath $archiveItem.FullName -Algorithm SHA256).Hash
        ArchiveBytes = $archiveItem.Length
        DatabaseBytes = $databaseItem.Length
        StaticFileCount = $staticCount
        ContainsEnv = $checks.Env
        ContainsDatabase = $checks.Database
        SensitiveSnapshot = $true
    }
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        $resolvedStage = (Resolve-Path -LiteralPath $stageRoot).Path
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not $resolvedStage.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean unexpected staging path: $resolvedStage"
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
