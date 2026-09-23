$ErrorActionPreference = 'Stop'
$script:Root = $PSScriptRoot
$script:State = Join-Path $script:Root 'instance\control-processes.json'
$script:Python = Join-Path $script:Root '.venv\Scripts\python.exe'
$script:Port = 18473

function Write-ControlLog([string]$Message) {
    $path = Join-Path $script:Root 'instance\control.log'
    if ((Test-Path $path) -and (Get-Item $path).Length -gt 1MB) { Move-Item -LiteralPath $path -Destination "$path.1" -Force }
    Add-Content -LiteralPath $path -Value "$(Get-Date -Format s) $Message" -Encoding UTF8
}
function Invoke-Runtime([string]$Command) {
    $result = & $script:Python (Join-Path $script:Root 'tools\control_runtime.py') $Command
    if ($LASTEXITCODE -ne 0) { throw '需要管理者完成首次設定，請檢查 Python、.env 與內部管理者。' }
    return ($result | ConvertFrom-Json)
}
function Get-Registry {
    if (Test-Path -LiteralPath $script:State) { return (Get-Content -Raw -LiteralPath $script:State | ConvertFrom-Json) }
    return [pscustomobject]@{ instance = ''; processes = @() }
}
function Save-Registry($Registry) {
    $Registry | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$script:State.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$script:State.tmp" -Destination $script:State -Force
}
function Test-Identity($Entry) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$Entry.id)" -ErrorAction SilentlyContinue
    return ($null -ne $p -and $p.ExecutablePath -eq $Entry.executable -and
        $p.CreationDate.ToUniversalTime().Ticks.ToString() -eq $Entry.created -and $p.CommandLine -eq $Entry.command)
}
function Get-Health([string]$Url) {
    try {
        $data = Invoke-RestMethod -Uri "$Url/healthz" -TimeoutSec 2 -ErrorAction Stop
        if ($data.application -eq 'MultiThreader' -and $data.protocol -eq 1) { return $data }
    } catch { }
    return $null
}
function Get-ControlStatus([switch]$Public) {
    $registry = Get-Registry
    $web = @($registry.processes | Where-Object { $_.role -eq 'web' -and (Test-Identity $_) }).Count -gt 0
    $worker = @($registry.processes | Where-Object { $_.role -eq 'worker' -and (Test-Identity $_) }).Count -gt 0
    $local = Get-Health "http://127.0.0.1:$script:Port"
    $online = $web -and $local -and $registry.instance -and $local.instance -eq $registry.instance
    $service = Get-Service -Name Cloudflared -ErrorAction SilentlyContinue
    $remote = $null
    if ($Public) { $remote = Get-Health 'https://multithreader.stack-base.com' }
    $active = 0
    if ($online -and -not $local.ready) { $active = (Invoke-Runtime status).active }
    [pscustomobject]@{
        Web = [bool]$online; Worker = [bool]($worker -and $online -and $local.worker_online)
        Draining = [bool]($online -and -not $local.ready); Active = $active; Tunnel = [string]$service.Status
        Public = [bool]($online -and $remote -and $remote.ready -and $remote.instance -eq $registry.instance)
    }
}
function Start-ManagedProcess([string]$Role, [string]$File, $Registry) {
    $scriptPath = Join-Path $script:Root $File
    foreach ($suffix in @('out', 'error')) {
        $log = Join-Path $script:Root "instance\$Role.$suffix.log"
        if (Test-Path $log) { Move-Item -LiteralPath $log -Destination "$log.1" -Force }
    }
    $oldInstance = $env:MULTITHREADER_INSTANCE
    try {
        $env:MULTITHREADER_INSTANCE = $Registry.instance
        $p = Start-Process -FilePath $script:Python -ArgumentList "`"$scriptPath`"" -WorkingDirectory $script:Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $script:Root "instance\$Role.out.log") -RedirectStandardError (Join-Path $script:Root "instance\$Role.error.log")
    } finally { $env:MULTITHREADER_INSTANCE = $oldInstance }
    # Record launcher and child identities at launch, never recursively kill a tree.
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $processes = @(Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id) OR ParentProcessId=$($p.Id)")
        foreach ($process in $processes) {
            if ($process.CommandLine -and $process.CommandLine.Contains($scriptPath) -and $process.ExecutablePath -and
                -not @($Registry.processes | Where-Object { $_.id -eq $process.ProcessId }).Count) {
                $Registry.processes = @($Registry.processes) + [pscustomobject]@{
                    role = $Role; id = $process.ProcessId; executable = $process.ExecutablePath
                    created = $process.CreationDate.ToUniversalTime().Ticks.ToString(); command = $process.CommandLine
                }
                Save-Registry $Registry
            }
        }
        Start-Sleep -Milliseconds 200
    }
}
function Set-Tunnel([bool]$Running) {
    # Unverified/shared connectors are excluded from stop/start scope.
    $configPath = Join-Path $script:Root 'instance\control-settings.json'
    if (-not (Test-Path $configPath)) { return }
    $config = Get-Content -Raw $configPath | ConvertFrom-Json
    if (-not $config.dedicatedTunnel) { return }
    $service = Get-CimInstance Win32_Service -Filter "Name='Cloudflared'"
    $hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($service.PathName)))
    if ($hash -ne $config.serviceHash) { throw 'Cloudflared 設定已變更，需要管理者重新確認專用入口。' }
    try { if ($Running) { Start-Service Cloudflared } else { Stop-Service Cloudflared } }
    catch { throw '需要管理者完成首次設定：目前帳號沒有 Cloudflared 服務控制權限。' }
}
function Invoke-ControlAction {
    param([ValidateSet('Start','Stop','Restart','CancelStop')][string]$Action, [int]$TimeoutSeconds = 120)
    $mutex = New-Object System.Threading.Mutex($false, 'Local\MultiThreaderControl18473')
    $locked = $false
    $fileLock = $null
    try {
        try { $locked = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw '另一個控制台正在操作，請稍候。' }
        if (-not (Test-Path $script:Python) -or -not (Test-Path (Join-Path $script:Root '.env'))) { throw '需要管理者完成首次設定：缺少 Python 或 .env。' }
        $null = New-Item -ItemType Directory -Path (Join-Path $script:Root 'instance') -Force
        try { $fileLock = [IO.File]::Open((Join-Path $script:Root 'instance\control.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw '另一個控制台正在操作，請稍候。' }
        $preflight = Invoke-Runtime preflight
        if ($preflight.port -ne $script:Port -or $preflight.host -ne '127.0.0.1') { throw '需要管理者完成首次設定：控制台要求 HOST=127.0.0.1 與 PORT=18473。' }
        Write-ControlLog "操作開始：$Action"
        if ($Action -eq 'CancelStop') { $null = Invoke-Runtime resume; return '已取消停止；如背景發文已退出，請按全部啟動。' }
        if ($Action -in @('Stop','Restart')) {
            $registry = Get-Registry
            $null = Invoke-Runtime drain
            $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
            do {
                $workers = @($registry.processes | Where-Object { $_.role -eq 'worker' -and (Test-Identity $_) })
                $runtime = Invoke-Runtime status
                if (-not $workers.Count -and $runtime.active -eq 0) { break }
                if ((Get-Date) -ge $deadline) { throw '等待發布完成逾時，已保留程序。可按取消停止，再按全部啟動恢復。' }
                Start-Sleep -Seconds 1
            } while ($true)
            $unknownWorker = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$runtime.worker_pid)" -ErrorAction SilentlyContinue
            if ($runtime.worker_online -and $unknownWorker) { throw '尚有未受控制台管理的背景程序，已保留入口；請由原啟動方式結束。' }
            foreach ($entry in @($registry.processes | Where-Object { $_.role -eq 'web' } | Sort-Object id -Descending)) {
                if (Test-Identity $entry) { Stop-Process -Id $entry.id -ErrorAction Stop }
            }
            if (Get-NetTCPConnection -LocalPort $script:Port -State Listen -ErrorAction SilentlyContinue) { throw '連接埠仍由未受管理的程序使用，已保留 Cloudflared 入口。' }
            Set-Tunnel $false
            Write-ControlLog '安全停止完成；未確認專用的 Cloudflared 保持原狀。'
        }
        if ($Action -in @('Start','Restart')) {
            $registry = Get-Registry
            $registry.processes = @($registry.processes | Where-Object { Test-Identity $_ })
            $web = @($registry.processes | Where-Object role -eq 'web').Count -gt 0
            $worker = @($registry.processes | Where-Object role -eq 'worker').Count -gt 0
            if (-not $web -and (Get-NetTCPConnection -LocalPort $script:Port -State Listen -ErrorAction SilentlyContinue)) { throw '18473 已被其他或舊版程序使用；請由原啟動方式結束後再試。' }
            $runtime = Invoke-Runtime status
            if (-not $worker -and $runtime.worker_online -and (Get-Process -Id $runtime.worker_pid -ErrorAction SilentlyContinue)) { throw '偵測到其他背景發文程序，請先由原啟動方式結束，稍後再試。' }
            if (-not $registry.processes.Count) { $registry.instance = [guid]::NewGuid().ToString() }
            Save-Registry $registry
            Set-Tunnel $true
            $null = Invoke-Runtime resume
            if (-not $web) { Start-ManagedProcess web app.py $registry }
            if (-not $worker) { Start-ManagedProcess worker worker.py $registry }
            $deadline = (Get-Date).AddSeconds(30)
            do {
                $state = Get-ControlStatus -Public
                if ($state.Web -and $state.Worker -and $state.Public) { break }
                Start-Sleep -Seconds 1
            } while ((Get-Date) -lt $deadline)
            if (-not ($state.Web -and $state.Worker -and $state.Public)) { throw '啟動未完全就緒：請查看網頁、背景發文及公開入口狀態。' }
            Write-ControlLog '全部啟動完成。'
        }
        return '操作完成；共用或未確認的 Cloudflared 不受啟停控制。'
    } catch {
        Write-ControlLog '操作未完成；請查看控制台狀態並檢查首次設定。'
        throw
    } finally { if ($fileLock) { $fileLock.Dispose() }; if ($locked) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
}
Export-ModuleMember -Function Invoke-ControlAction, Get-ControlStatus
