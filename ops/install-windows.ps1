#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ConfigPath)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows 10/11 with your normal user account.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$gatewayTask = "LocalQwenApi-Gateway-$sid"
$tunnelTask = "LocalQwenApi-Tunnel-$sid"
$marker = "Managed by local-qwen-api for $sid"
$source = Split-Path -Parent $PSScriptRoot
$app = Join-Path $env:LOCALAPPDATA 'LocalQwenApi'
$server = Join-Path $app 'server.py'
$key = Join-Path $app 'api-key'

$config = Get-Item -LiteralPath ([Environment]::ExpandEnvironmentVariables($ConfigPath))
if ($config -isnot [IO.FileInfo]) { throw 'Select the existing named tunnel config.yml file.' }
$configPath = $config.FullName
$cloudflared = (Get-Command cloudflared.exe -CommandType Application -ErrorAction Stop).Path
& $cloudflared tunnel --config $configPath ingress validate
if ($LASTEXITCODE -ne 0) { throw 'cloudflared rejected the tunnel configuration.' }

# Resolve a real interpreter, not py.exe, a Store alias, or a venv launcher.
$python = $null
$probe = "import json,sys; assert sys.version_info >= (3,9); print(json.dumps(getattr(sys, '_base_executable', sys.executable)))"
foreach ($candidate in 'py.exe', 'python.exe', 'python3.exe') {
    $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue
    if (!$command) { continue }
    $arguments = @('-c', $probe)
    if ($candidate -eq 'py.exe') { $arguments = @('-3') + $arguments }
    try { $result = & $command.Path @arguments 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0 -and $result) {
        $path = $result | Select-Object -Last 1 | ConvertFrom-Json
        if (Test-Path -LiteralPath $path -PathType Leaf) { $python = $path; break }
    }
}
if (!$python) { throw 'Install Python 3.9 or later and make py or python available on PATH.' }
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (Test-Path -LiteralPath $pythonw -PathType Leaf) { $python = $pythonw }

foreach ($name in 'server.py', 'web\index.html') {
    if (!(Test-Path -LiteralPath (Join-Path $source $name) -PathType Leaf)) {
        throw "Project file missing: $name"
    }
}

# At every logon verify the owner of the listener before opening the tunnel.
$waitForGateway = {
    param($serverPath, $pythonPath)
    for ($i = 0; $i -lt 30; $i++) {
        $listeners = @(Get-NetTCPConnection -LocalPort 18435 -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count) {
            if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') {
                throw 'Unexpected listener on port 18435; tunnel was not started.'
            }
            $ownerId = $listeners[0].OwningProcess
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
            if ($process.ExecutablePath -ine $pythonPath -or !$process.CommandLine -or
                $process.CommandLine.IndexOf(('"' + $serverPath + '"'), [StringComparison]::OrdinalIgnoreCase) -lt 0) {
                throw 'Port 18435 belongs to another process; tunnel was not started.'
            }
            return
        }
        Start-Sleep -Seconds 1
    }
    throw 'Gateway did not start within 30 seconds. Check its task result and API key file.'
}

# The tunnel action is self-contained, even if the original checkout is moved.
function Quote-PSString([string]$Value) { "'" + $Value.Replace("'", "''") + "'" }
$runner = @"
`$ErrorActionPreference = 'Stop'
& { $waitForGateway } $(Quote-PSString $server) $(Quote-PSString $python)
& $(Quote-PSString $cloudflared) tunnel --config $(Quote-PSString $configPath) run
exit `$LASTEXITCODE
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($runner))
$windowsPS = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$gatewayAction = New-ScheduledTaskAction -Execute $python -WorkingDirectory $app -Argument ('"{0}" --port 18435 --key-file "{1}"' -f $server, $key)
$tunnelAction = New-ScheduledTaskAction -Execute $windowsPS -WorkingDirectory $app -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $encoded"
$register = @{
    TaskPath = '\'
    Description = $marker
    Principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    Trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
    Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
    Force = $true
}

$mutex = [Threading.Mutex]::new($false, "Local\LocalQwenApi-Install-$sid")
try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
if (!$locked) { $mutex.Dispose(); throw 'Another installation is in progress.' }
try {
    $previous = @(foreach ($taskName in $tunnelTask, $gatewayTask) {
        $task = Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            if ($task.Description -ne $marker) { throw "Unrelated task already exists: $taskName" }
            $task
        }
    })
    foreach ($task in $previous) {
        if ($task.State -in 'Running', 'Queued') { Stop-ScheduledTask -InputObject $task }
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (!(Get-NetTCPConnection -LocalPort 18435 -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($i -eq 20) { throw 'Port 18435 is in use. Stop the manually started gateway and retry.' }

    [void][IO.Directory]::CreateDirectory((Join-Path $app 'web'))
    foreach ($name in 'server.py', 'web\index.html') {
        $destination = Join-Path $app $name
        $staged = $destination + '.install-tmp'
        try {
            Copy-Item -LiteralPath (Join-Path $source $name) -Destination $staged -Force
            if ([IO.File]::Exists($destination)) {
                [IO.File]::Replace($staged, $destination, [Management.Automation.Language.NullString]::Value)
            } else {
                [IO.File]::Move($staged, $destination)
            }
        } finally {
            if ([IO.File]::Exists($staged)) { [IO.File]::Delete($staged) }
        }
    }
    Register-ScheduledTask @register -TaskName $gatewayTask -Action $gatewayAction | Out-Null
    Register-ScheduledTask @register -TaskName $tunnelTask -Action $tunnelAction | Out-Null
    Start-ScheduledTask -TaskPath '\' -TaskName $gatewayTask
    & $waitForGateway $server $python
    Start-ScheduledTask -TaskPath '\' -TaskName $tunnelTask
    Write-Output "Installed login tasks: $gatewayTask, $tunnelTask"
    Write-Output "Gateway: http://127.0.0.1:18435/   API key file: $key"
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
