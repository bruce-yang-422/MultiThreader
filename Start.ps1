param([int]$TimeoutSeconds = 120)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ServiceControl.psm1') -Force
Invoke-ControlAction Start -TimeoutSeconds $TimeoutSeconds
