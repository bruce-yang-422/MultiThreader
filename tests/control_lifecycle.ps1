# Isolated demo processes only. Does not control the installed Cloudflared service.
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('multithreader-control-' + [guid]::NewGuid())
$null = New-Item -ItemType Directory -Path $fixture
$null = New-Item -ItemType Directory -Path (Join-Path $fixture 'tools')
foreach ($name in @('app.py','worker.py','manage.py','multithreader')) { Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $fixture -Recurse }
Copy-Item -LiteralPath (Join-Path $repo 'tools\control_runtime.py') -Destination (Join-Path $fixture 'tools')
Set-Content -LiteralPath (Join-Path $fixture '.env') -Value 'THREADS_MODE=demo'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$oldMode = $env:THREADS_MODE
$oldPort = $env:PORT
$oldHostSetting = $env:HOST
$env:THREADS_MODE = 'demo'
$env:PORT = '19473'
$env:HOST = '127.0.0.1'
$module = Import-Module (Join-Path $repo 'ServiceControl.psm1') -Force -PassThru
try {
    if (Get-NetTCPConnection -LocalPort 19473 -State Listen -ErrorAction SilentlyContinue) { throw 'Test port is occupied.' }
    $null = & $python (Join-Path $fixture 'manage.py') init --username controltest --generate-password
    if ($LASTEXITCODE -ne 0) { throw 'Fixture initialization failed.' }
    & $module { param($root, $python)
        $script:Root = $root
        $script:Python = $python
        $script:State = Join-Path $root 'instance\control-processes.json'
        $script:Port = 19473
        function script:Set-Tunnel([bool]$Running) { }
        function script:Get-Health([string]$Url) {
            if ($script:breakPublic -and $Url.StartsWith('https:')) { return $null }
            try { Invoke-RestMethod 'http://127.0.0.1:19473/healthz' -TimeoutSec 2 } catch { $null }
        }
    } $fixture $python
    $null = Invoke-ControlAction Start
    $first = Get-Content -Raw (Join-Path $fixture 'instance\control-processes.json') | ConvertFrom-Json
    $null = Invoke-ControlAction Start
    $second = Get-Content -Raw (Join-Path $fixture 'instance\control-processes.json') | ConvertFrom-Json
    if (($first.processes.id -join ',') -ne ($second.processes.id -join ',')) { throw 'Repeated start created extra processes.' }
    $state = Get-ControlStatus -Public
    if (-not ($state.Web -and $state.Worker -and $state.Public)) { throw 'Started processes are not healthy.' }
    & $module { $script:breakPublic = $true }
    $state = Get-ControlStatus -Public
    if ($state.Public -or -not $state.Web) { throw 'Public outage was not detected independently.' }
    & $module { $script:breakPublic = $false }
    & $module {
        $entry = (Get-Registry).processes[0]
        $entry.created = '0'
        if (Test-Identity $entry) { throw 'Reused PID was accepted.' }
    }
    & $module {
        $script:runtimeImplementation = ${function:Invoke-Runtime}
        function script:Invoke-Runtime([string]$Command) {
            $result = & $script:runtimeImplementation $Command
            if ($Command -eq 'status') { $result.active = 1 }
            return $result
        }
    }
    $timedOut = $false
    try { $null = Invoke-ControlAction Stop -TimeoutSeconds 0 } catch { $timedOut = $true }
    if (-not $timedOut -or -not (Get-ControlStatus).Web) { throw 'Drain timeout did not preserve the web process.' }
    & $module { Set-Item Function:script:Invoke-Runtime $script:runtimeImplementation }
    Start-Sleep -Seconds 3
    $state = Get-ControlStatus
    if ($state.Worker) { throw 'Exited worker was reported as healthy.' }
    $null = Invoke-ControlAction CancelStop
    $null = Invoke-ControlAction Start
    $null = Invoke-ControlAction Stop -TimeoutSeconds 15
    $state = Get-ControlStatus
    if ($state.Web -or $state.Worker) { throw 'Processes survived safe stop.' }
    $null = Invoke-ControlAction Start
    $null = Invoke-ControlAction Restart -TimeoutSeconds 15
    $null = Invoke-ControlAction Stop -TimeoutSeconds 15
    'PASS: start, duplicate start, identity mismatch, public outage, missing worker, drain timeout, cancel, safe stop, restart (isolated demo).'
} finally {
    & $module {
        foreach ($entry in (Get-Registry).processes) {
            if (Test-Identity $entry) { Stop-Process -Id $entry.id -ErrorAction SilentlyContinue }
        }
    }
    $env:THREADS_MODE = $oldMode
    $env:PORT = $oldPort
    $env:HOST = $oldHostSetting
    # Only this explicitly generated temp fixture can be removed.
    $resolved = [IO.Path]::GetFullPath($fixture)
    $prefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\multithreader-control-'
    if ($resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
