[CmdletBinding()]
param(
    [switch]$SkipAkshareUpdate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = $PSScriptRoot
$FrontendDir = Join-Path $ProjectRoot "frontend"
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendBuildDir = Join-Path $FrontendDir "build"
$BackendStaticDir = Join-Path $BackendDir "static"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv-cache"

function Get-RequiredCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$InstallHint
    )

    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $Command) {
        throw "Required command '$Name' was not found. $InstallHint"
    }

    return $Command.Source
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,

        [Parameter(ValueFromRemainingArguments)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "'$FilePath $($ArgumentList -join ' ')' failed with exit code $LASTEXITCODE."
    }
}

try {
    $Npm = Get-RequiredCommand "npm.cmd" "Install Node.js, then open a new terminal."
    $Uv = Get-RequiredCommand "uv.exe" "Install uv from https://docs.astral.sh/uv/."

    $RequiredFrontendFiles = @(
        (Join-Path $FrontendDir "node_modules\.bin\react-scripts.cmd"),
        (Join-Path $FrontendDir "node_modules\rollup-plugin-terser\node_modules\jest-worker\build\index.js")
    )
    $FrontendDependenciesReady = -not ($RequiredFrontendFiles.Where({
        -not (Test-Path $_)
    }, "First"))

    if (-not $FrontendDependenciesReady) {
        Write-Host "Installing frontend dependencies..."
        Push-Location $FrontendDir
        try {
            Invoke-CheckedCommand $Npm "ci" "--cache" $NpmCacheDir
        }
        finally {
            Pop-Location
        }
    }

    Write-Host "Building the frontend for production..."
    Push-Location $FrontendDir
    try {
        Invoke-CheckedCommand $Npm "run" "build"
    }
    finally {
        Pop-Location
    }

    Write-Host "Copying frontend build to backend..."
    if (Test-Path $BackendStaticDir) {
        Remove-Item -LiteralPath $BackendStaticDir -Recurse -Force
    }
    Move-Item -LiteralPath $FrontendBuildDir -Destination $BackendStaticDir

    Push-Location $BackendDir
    try {
        $BackendPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
        if (-not (Test-Path $BackendPython)) {
            Write-Host "Installing backend dependencies..."
            Invoke-CheckedCommand $Uv "sync"
        }

        if (-not $SkipAkshareUpdate) {
            Write-Host "Updating akshare library with uv..."
            Invoke-CheckedCommand $Uv "pip" "install" "--upgrade" "akshare"
        }

        Write-Host "Starting Quant Compass at http://localhost:8666"
        Invoke-CheckedCommand $Uv "run" "uvicorn" "main:app" "--host" "127.0.0.1" "--port" "8666"
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Error $_
    exit 1
}
