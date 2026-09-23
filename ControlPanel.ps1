param([switch]$SmokeTest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$script:module = Join-Path $PSScriptRoot 'ServiceControl.psm1'
$script:tasks = @{}
$script:lastLocal = [datetime]::MinValue
$script:lastPublic = [datetime]::MinValue
$script:publicOK = $false
$script:buttons = @()
$form = New-Object Windows.Forms.Form
$form.Text = 'MultiThreader 多脆客 控制台'
$form.ClientSize = New-Object Drawing.Size(570, 445)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object Drawing.Font('Microsoft JhengHei UI', 11)
function New-Label([int]$Y, [string]$Text) {
    $label = New-Object Windows.Forms.Label
    $label.Location = New-Object Drawing.Point(22, $Y)
    $label.Size = New-Object Drawing.Size(530, 30)
    $label.Text = $Text
    $form.Controls.Add($label)
    return $label
}
$tunnel = New-Label 20 '網路入口：檢查中'
$overall = New-Label 55 '多脆客：檢查中'
$web = New-Label 90 '網頁：檢查中'
$worker = New-Label 125 '背景發文：檢查中'
function Begin-Task([string]$Name, [string]$Action) {
    $ps = [PowerShell]::Create()
    $null = $ps.AddScript({ param($module, $name, $action)
        Import-Module $module -Force
        if ($name -eq 'action') { Invoke-ControlAction $action }
        elseif ($name -eq 'public') { Get-ControlStatus -Public }
        else { Get-ControlStatus }
    }).AddArgument($script:module).AddArgument($Name).AddArgument($Action)
    $script:tasks[$Name] = @{ PowerShell = $ps; Handle = $ps.BeginInvoke() }
}
function New-ActionButton([int]$X, [int]$Y, [string]$Text, [string]$Action) {
    $button = New-Object Windows.Forms.Button
    $button.Location = New-Object Drawing.Point($X, $Y)
    $button.Size = New-Object Drawing.Size(160, 38)
    $button.Text = $Text
    $button.Tag = $Action
    $button.Add_Click({
        if ($script:tasks.ContainsKey('action')) { return }
        foreach ($b in $script:buttons) { $b.Enabled = $false }
        $recent.Text = if ($this.Tag -in @('Stop','Restart')) { '停止中：等待正在發布的項目完成…' } else { '操作中，請稍候…' }
        $progress.Style = 'Marquee'
        Begin-Task action $this.Tag
    })
    $form.Controls.Add($button)
    $script:buttons += $button
}
New-ActionButton 22 175 '全部啟動' Start
New-ActionButton 200 175 '全部停止' Stop
New-ActionButton 378 175 '全部重啟' Restart
New-ActionButton 378 223 '取消停止' CancelStop
$open = New-Object Windows.Forms.Button
$open.Location = New-Object Drawing.Point(22, 223)
$open.Size = New-Object Drawing.Size(160, 38)
$open.Text = '開啟多脆客'
$open.Add_Click({ Start-Process 'https://multithreader.stack-base.com' })
$form.Controls.Add($open)
$logs = New-Object Windows.Forms.Button
$logs.Location = New-Object Drawing.Point(200, 223)
$logs.Size = New-Object Drawing.Size(160, 38)
$logs.Text = '查看問題紀錄'
$logs.Add_Click({
    $dialog = New-Object Windows.Forms.Form
    $dialog.Text = '問題紀錄（不含帳號或憑證）'
    $dialog.Size = New-Object Drawing.Size(660, 440)
    $box = New-Object Windows.Forms.TextBox
    $box.Multiline = $true
    $box.ReadOnly = $true
    $box.ScrollBars = 'Vertical'
    $box.Dock = 'Fill'
    $path = Join-Path $PSScriptRoot 'instance\control.log'
    $box.Text = if (Test-Path $path) { (Get-Content -LiteralPath $path -Tail 100) -join "`r`n" } else { '尚無操作紀錄。' }
    $detail = New-Object Windows.Forms.Button
    $detail.Text = '查看詳細狀態'
    $detail.Dock = 'Bottom'
    $detail.Height = 36
    $detail.Add_Click({ $box.Text = "最近操作：$($recent.Text)`r`n`r`n" + ($script:lastState | ConvertTo-Json) })
    $dialog.Controls.Add($box)
    $dialog.Controls.Add($detail)
    $null = $dialog.ShowDialog($form)
    $dialog.Dispose()
})
$form.Controls.Add($logs)
$null = New-Label 275 'multithreader.stack-base.com  |  本機 127.0.0.1:18473'
$recent = New-Label 310 '最近操作：尚無'
$recent.Height = 55
$progress = New-Object Windows.Forms.ProgressBar
$progress.Location = New-Object Drawing.Point(22, 370)
$progress.Size = New-Object Drawing.Size(516, 12)
$form.Controls.Add($progress)
$null = New-Label 395 '關閉視窗不會停止服務；未確認專用的入口不會被停止。'
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    foreach ($name in @($script:tasks.Keys)) {
        $task = $script:tasks[$name]
        if (-not $task.Handle.IsCompleted) { continue }
        try {
            $result = @($task.PowerShell.EndInvoke($task.Handle))
            if ($task.PowerShell.HadErrors) { throw '背景操作失敗' }
            if ($name -eq 'action') { $recent.Text = '最近操作：' + ($result -join ' ') }
            elseif ($result.Count) {
                $s = $result[-1]
                $script:lastState = $s
                if ($name -eq 'public') { $script:publicOK = $s.Public }
                $tunnel.Text = "網路入口：服務 $($s.Tunnel)；公開連線 " + $(if ($script:publicOK) { '正常' } else { '未就緒' })
                $web.Text = '網頁：' + $(if ($s.Web) { '已啟動' } else { '已停止或未受管理' })
                $worker.Text = '背景發文：' + $(if ($s.Worker) { '已啟動' } else { '已停止或尚未就緒' })
                $ready = $s.Web -and $s.Worker -and $script:publicOK -and -not $s.Draining
                $overall.Text = '多脆客：' + $(if ($s.Draining) { '停止中' } elseif ($ready) { '可使用' } elseif (-not $s.Web -and -not $s.Worker) { '已停止或未受管理' } else { '部分異常' })
                $overall.ForeColor = if ($ready) { [Drawing.Color]::ForestGreen } else { [Drawing.Color]::DarkOrange }
                if ($s.Draining -and $script:tasks.ContainsKey('action')) { $recent.Text = "正在完成 $($s.Active) 個發布項目，請稍候。" }
            }
        } catch {
            if ($name -eq 'action') {
                $recent.Text = '操作未完成，請檢查狀態或由管理者確認首次設定。'
                if ($task.PowerShell.Streams.Error.Count) {
                    $message = $task.PowerShell.Streams.Error[0].Exception.Message
                    # Only our fixed Chinese lifecycle errors are shown; no raw URLs or credentials.
                    if ($message -match '^(需要管理者|另一個控制台|等待發布|尚有未受|連接埠仍|18473|偵測到其他|Cloudflared 設定|啟動未完全)') { $recent.Text = $message }
                }
            } else { $overall.Text = '狀態檢查失敗'; $script:publicOK = $false }
        } finally {
            $task.PowerShell.Dispose()
            $script:tasks.Remove($name)
            if ($name -eq 'action') {
                foreach ($b in $script:buttons) { $b.Enabled = $true }
                $progress.Style = 'Blocks'
                $script:lastLocal = [datetime]::MinValue
            }
        }
    }
    if (-not $script:tasks.ContainsKey('local') -and ((Get-Date) - $script:lastLocal).TotalSeconds -ge 3) { $script:lastLocal = Get-Date; Begin-Task local '' }
    if (-not $script:tasks.ContainsKey('public') -and ((Get-Date) - $script:lastPublic).TotalSeconds -ge 15) { $script:lastPublic = Get-Date; Begin-Task public '' }
})
$form.Add_FormClosing({
    if ($script:tasks.ContainsKey('action')) { $_.Cancel = $true; $recent.Text = '請等待本次操作完成或逾時後再關閉視窗。' }
})
$form.Add_FormClosed({ $timer.Stop(); foreach ($task in $script:tasks.Values) { $task.PowerShell.Stop(); $task.PowerShell.Dispose() }; $timer.Dispose() })
if ($SmokeTest) { $form.Dispose(); 'ControlPanel UI constructed'; exit }
$timer.Start()
[Windows.Forms.Application]::Run($form)
