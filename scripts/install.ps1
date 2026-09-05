param(
    [switch]$Local,
    [Alias("global")]
    [switch]$GlobalInstall,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host "BuildAnchor installer"
    Write-Host "Usage: .\scripts\install.ps1 -Local -Global"
    Write-Host "  -Local  Install the checkout containing this script."
    Write-Host "  -Global Install the command for the current user."
    exit 0
}

$repositoryUrl = if ($env:BUILDANCHOR_SOURCE_URL) {
    $env:BUILDANCHOR_SOURCE_URL
} else {
    "https://github.com/tensilestream/buildanchor/archive/refs/heads/main.zip"
}

$repositoryRoot = $null
if ($PSScriptRoot) {
    $candidate = Split-Path -Parent $PSScriptRoot
    if ((Test-Path (Join-Path $candidate "pyproject.toml")) -and
        (Test-Path (Join-Path $candidate "src\buildanchor"))) {
        $repositoryRoot = $candidate
    }
}

$isLocalCheckout = [bool]$repositoryRoot
if ($Local -and -not $isLocalCheckout) {
    throw "-Local must be run from a BuildAnchor checkout."
}

if (-not $Local -and -not $GlobalInstall) {
    $GlobalInstall = $true
}

$source = if ($repositoryRoot) { $repositoryRoot } else { $repositoryUrl }
$isEditable = [bool]$repositoryRoot

if ($GlobalInstall -and (Get-Command pipx -ErrorAction SilentlyContinue)) {
    if ($isEditable) {
        pipx install --editable --force $source
    } else {
        pipx install --force $source
    }
    pipx ensurepath | Out-Null
} else {
    $pythonCmd = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { $null }
    if (-not $pythonCmd) {
        throw "BuildAnchor requires pipx or Python 3.10+."
    }
    $pipArgs = @("-m", "pip", "install", "--upgrade")
    if ($GlobalInstall) {
        $pipArgs += "--user"
    }
    if ($isEditable) {
        $pipArgs += "--editable"
    }
    $pipArgs += $source
    & $pythonCmd $pipArgs
}

Write-Host "BuildAnchor installed. Run: buildanchor --help"
