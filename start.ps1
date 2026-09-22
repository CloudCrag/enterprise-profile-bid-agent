[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$RuntimeRoot = Join-Path $ProjectRoot 'runtime_data'
$LogRoot = Join-Path $RuntimeRoot 'audit\service_logs'
$ErrorPage = Join-Path $RuntimeRoot 'startup_error.html'
$PreflightReport = Join-Path $RuntimeRoot 'startup_preflight.json'
$Processes = New-Object System.Collections.Generic.List[System.Diagnostics.Process]

function ConvertTo-HtmlText([string]$Value) {
    return [System.Net.WebUtility]::HtmlEncode($Value)
}

function Show-StartupFailure([string]$Title, [string]$Message, [string]$Suggestion) {
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    $safeTitle = ConvertTo-HtmlText $Title
    $safeMessage = ConvertTo-HtmlText $Message
    $safeSuggestion = ConvertTo-HtmlText $Suggestion
    $html = @"
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>系统启动失败</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;background:#f5f7fb;margin:0;padding:48px;color:#172033}.card{max-width:840px;margin:auto;background:#fff;border-radius:16px;padding:32px;box-shadow:0 12px 36px rgba(18,38,63,.12);border-top:6px solid #c62828}h1{margin-top:0;color:#b71c1c}.label{font-weight:700;margin-top:24px}.box{background:#fff5f5;border:1px solid #ffcdd2;border-radius:10px;padding:16px;white-space:pre-wrap}.tip{background:#f4f8ff;border:1px solid #c8dcff;border-radius:10px;padding:16px;white-space:pre-wrap}code{background:#eef2f7;padding:2px 6px;border-radius:4px}</style></head>
<body><div class="card"><h1>系统启动失败</h1><div class="label">失败项</div><div class="box">$safeTitle</div><div class="label">实际原因</div><div class="box">$safeMessage</div><div class="label">处理建议</div><div class="tip">$safeSuggestion</div><p>处理完成后，请重新运行 <code>.\start.ps1</code>。</p></div></body></html>
"@
    [System.IO.File]::WriteAllText($ErrorPage, $html, [System.Text.UTF8Encoding]::new($false))
    try { Start-Process $ErrorPage | Out-Null } catch {}
    Write-Host "`n系统启动失败：$Title" -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    Write-Host $Suggestion -ForegroundColor Yellow
}

function Import-LocalEnvironment([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "缺少 .env.local。请复制 .env.example 为 .env.local，并填写真实智谱 GLM 与远程数据库配置。"
    }
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).Trim() }
        $index = $line.IndexOf('=')
        if ($index -lt 1) { continue }
        $name = $line.Substring(0, $index).Trim()
        $value = $line.Substring($index + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}


function Get-EnvValue([string]$Name, [string]$DefaultValue) {
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) { return $DefaultValue }
    return $value
}

function Require-Command([string]$Name, [string]$Suggestion) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "未找到 $Name。$Suggestion" }
    return $command.Source
}

function Test-PortFree([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return -not [bool]$listener
}

function Wait-Health([string]$Name, [string]$Url, [int]$Attempts = 60) {
    for ($i = 1; $i -le $Attempts; $i++) {
        foreach ($process in $Processes) {
            if ($process.HasExited) { throw "$Name 启动进程已退出，退出码 $($process.ExitCode)。" }
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "$Name 未在规定时间内通过健康检查：$Url"
}

function Start-ServiceProcess([string]$Name, [string]$FilePath, [string[]]$Arguments) {
    $stdout = Join-Path $LogRoot "$Name.out.log"
    $stderr = Join-Path $LogRoot "$Name.err.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $Processes.Add($process)
    return $process
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    Show-StartupFailure '操作系统不受支持' '当前版本只支持 Windows PowerShell。' '请在 Windows 10/11 环境中运行。'
    exit 1
}

try {
    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
    Import-LocalEnvironment (Join-Path $ProjectRoot '.env.local')

    $pythonSystem = Require-Command 'python' '请安装 Python 3.11 或更高版本，并加入 PATH。'
    $node = Require-Command 'node' '请安装 Node.js 18 或更高版本，并加入 PATH。'
    $npm = Require-Command 'npm.cmd' '请安装包含 npm 的 Node.js。'

    $ports = @(
        [int](Get-EnvValue 'PROFILE_SERVICE_PORT' '8000'),
        [int](Get-EnvValue 'BID_DECISION_SERVICE_PORT' '8001'),
        [int](Get-EnvValue 'API_PORT' '3000'),
        [int](Get-EnvValue 'WEB_PORT' '5173')
    )
    foreach ($port in $ports) {
        if (-not (Test-PortFree $port)) { throw "端口 $port 已被其他程序占用。请先停止占用进程。" }
    }

    $venvRoot = Join-Path $ProjectRoot '.venv'
    $python = Join-Path $venvRoot 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        & $pythonSystem -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
    }
    $requirements = Join-Path $ProjectRoot 'requirements-lock.txt'
    $requirementsHash = (Get-FileHash -Algorithm SHA256 $requirements).Hash
    $requirementsStamp = Join-Path $venvRoot '.requirements.sha256'
    $installedHash = if (Test-Path $requirementsStamp) { (Get-Content $requirementsStamp -Raw).Trim() } else { '' }
    if ($installedHash -ne $requirementsHash) {
        & $python -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw '安装 Python 依赖失败。请检查网络和 requirements-lock.txt。' }
        [System.IO.File]::WriteAllText($requirementsStamp, $requirementsHash)
    }

    foreach ($appPath in @('apps\api', 'apps\web')) {
        $fullPath = Join-Path $ProjectRoot $appPath
        if (-not (Test-Path (Join-Path $fullPath 'node_modules'))) {
            & $npm --prefix $fullPath ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "安装 $appPath 的 Node 依赖失败。" }
        }
    }

    & $python (Join-Path $ProjectRoot 'scripts\preflight.py')
    if ($LASTEXITCODE -ne 0) {
        $reason = '核心依赖检查失败。'
        if (Test-Path $PreflightReport) {
            try {
                $report = Get-Content $PreflightReport -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($report.error.message) { $reason = [string]$report.error.message }
            } catch {}
        }
        Show-StartupFailure '核心依赖检查未通过' $reason '检查 .env.local、手动 SSH 隧道、远程数据库账号、智谱 API Key 与模型权限。'
        exit 1
    }

    Get-ChildItem (Join-Path $RuntimeRoot 'enterprise_source') -Recurse -File | ForEach-Object {
        try { $_.IsReadOnly = $true } catch {}
    }

    $profilePort = [int](Get-EnvValue 'PROFILE_SERVICE_PORT' '8000')
    $bidPort = [int](Get-EnvValue 'BID_DECISION_SERVICE_PORT' '8001')
    $apiPort = [int](Get-EnvValue 'API_PORT' '3000')
    $webPort = [int](Get-EnvValue 'WEB_PORT' '5173')

    Start-ServiceProcess 'profile-service' $python @('-m','uvicorn','services.profile_agent_api.app.main:app','--host','127.0.0.1','--port',"$profilePort") | Out-Null
    Wait-Health '企业画像服务' "http://127.0.0.1:$profilePort/internal/health"

    Start-ServiceProcess 'bid-service' $python @('-m','uvicorn','src.api.app:app','--host','127.0.0.1','--port',"$bidPort") | Out-Null
    Wait-Health '投标决策服务' "http://127.0.0.1:$bidPort/health"

    Start-ServiceProcess 'node-api' $node @('apps/api/src/index.mjs') | Out-Null
    Wait-Health '统一 API' "http://127.0.0.1:$apiPort/api/system/readiness"

    Start-ServiceProcess 'web' $npm @('--prefix','apps/web','run','dev','--','--host','127.0.0.1','--port',"$webPort") | Out-Null
    Wait-Health '前端页面' "http://127.0.0.1:$webPort/"

    Write-Host "`n企业画像与投标决策系统已启动：http://127.0.0.1:$webPort" -ForegroundColor Green
    Write-Host '按 Ctrl+C 停止全部服务。' -ForegroundColor Cyan
    Start-Process "http://127.0.0.1:$webPort" | Out-Null

    while ($true) {
        foreach ($process in $Processes) {
            if ($process.HasExited) { throw "服务进程异常退出，退出码 $($process.ExitCode)。请查看 runtime_data\audit\service_logs。" }
        }
        Start-Sleep -Seconds 2
    }
} catch {
    Show-StartupFailure '本机启动失败' $_.Exception.Message '检查错误页面中的原因和 runtime_data\audit\service_logs，再重新运行。'
    exit 1
} finally {
    foreach ($process in $Processes) {
        try {
            if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        } catch {}
    }
}
