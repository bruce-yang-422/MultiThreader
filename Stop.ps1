param([int]$TimeoutSeconds = 120, [switch]$Cancel)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ServiceControl.psm1') -Force
if ($Cancel) { Invoke-ControlAction CancelStop } else { Invoke-ControlAction Stop -TimeoutSeconds $TimeoutSeconds }
