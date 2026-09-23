param([switch]$DedicatedTunnelConfirmed)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python) -or -not (Test-Path (Join-Path $PSScriptRoot '.env'))) { throw '請先依 README 建立 Python 環境與 .env。' }
$null = & $python (Join-Path $PSScriptRoot 'tools\control_runtime.py') preflight
if ($LASTEXITCODE -ne 0) { throw '請先執行 python manage.py init 建立內部管理者。' }
$service = Get-CimInstance Win32_Service -Filter "Name='Cloudflared'"
$hash = ''
if ($DedicatedTunnelConfirmed) {
    if (-not $service) { throw '尚未安裝 Cloudflared 服務。' }
    # Remotely managed connectors encode the tunnel UUID in their service token.
    $tunnelId = ''
    if ($service.PathName -match '--token[=\s]+"?([^"\s]+)') {
        try { $tokenData = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Matches[1])) | ConvertFrom-Json; $tunnelId = $tokenData.t } catch { }
    }
    if ($tunnelId -ne 'f0c5c608-6c46-4903-b75b-7f9fb3f4dd4e') { throw '無法驗證預期 Tunnel ID，保留原服務設定。' }
    $hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($service.PathName)))
}
$settings = Join-Path $PSScriptRoot 'instance\control-settings.json'
# Reinstalling a shortcut must not silently remove an existing administrator choice.
if ($DedicatedTunnelConfirmed -or -not (Test-Path $settings)) {
    @{ dedicatedTunnel = [bool]$DedicatedTunnelConfirmed; serviceHash = $hash } | ConvertTo-Json | Set-Content -LiteralPath $settings -Encoding UTF8
}
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'MultiThreader 控制台.lnk'))
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'ControlPanel.ps1')`""
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'MultiThreader 多脆客啟停控制台'
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Output '已建立桌面控制台捷徑。共用或未確認專用的 Cloudflared 僅監看，不會停止。'
