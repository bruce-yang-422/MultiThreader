$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install requirements.txt first. See README.md.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
& $pythonPath manage.py init
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed.' }
$workerProcess = Start-Process -FilePath $pythonPath -ArgumentList 'worker.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput 'instance\worker.out.log' -RedirectStandardError 'instance\worker.error.log'
try { & $pythonPath app.py }
finally { if (-not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id } }
