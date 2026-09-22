$ErrorActionPreference = 'Stop'
$registryPath = Join-Path $PSScriptRoot 'instance\processes.json'
if (-not (Test-Path -LiteralPath $registryPath)) { Write-Host 'No recorded background services.'; exit }
$registered = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
$workspacePrefix = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
function Stop-ServiceTree([int]$rootProcessId) {
    # Windows venv Python uses a launcher plus a child Python process.
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$rootProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ServiceTree ([int]$child.ProcessId) }
    Stop-Process -Id $rootProcessId -ErrorAction SilentlyContinue
}
foreach ($entry in $registered) {
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$entry.id)" -ErrorAction SilentlyContinue
    if ($null -eq $serviceProcess) { continue }
    if ($serviceProcess.ExecutablePath -and $serviceProcess.ExecutablePath.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase) -and $serviceProcess.CommandLine.Contains([string]$entry.script)) {
        Stop-ServiceTree ([int]$entry.id)
        Write-Host "Stopped $($entry.script)"
    } else { Write-Host "Skipped PID $($entry.id): process identity changed." }
}
