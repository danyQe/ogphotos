<#
.SYNOPSIS
  Build the OGphotos Windows installer: dist\OGphotos-Setup-<Version>.exe
.EXAMPLE
  powershell -File packaging\windows\build.ps1 -Version 0.1.0
#>
param(
    [Parameter(Mandatory = $true)][string]$Version
)
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Dist = Join-Path $Root 'dist'
$Stage = Join-Path $Dist 'stage-windows'
New-Item -ItemType Directory -Force $Dist | Out-Null

Push-Location $Root
try {
    uv run python (Join-Path $Root 'packaging\stage.py') --target windows --out $Stage
    if ($LASTEXITCODE -ne 0) { throw 'stage.py failed' }

    $Iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
    if (-not (Test-Path $Iscc)) {
        choco install innosetup -y --no-progress
        if ($LASTEXITCODE -ne 0) { throw 'choco install innosetup failed' }
    }
    if (-not (Test-Path $Iscc)) { throw "ISCC.exe not found at $Iscc" }

    & $Iscc "/DAppVersion=$Version" "/DStageDir=$Stage" "/DOutDir=$Dist" (Join-Path $PSScriptRoot 'ogphotos.iss')
    if ($LASTEXITCODE -ne 0) { throw 'ISCC failed' }
    Write-Host (Join-Path $Dist "OGphotos-Setup-$Version.exe")
} finally {
    Pop-Location
}
