<#
.SYNOPSIS
    启动 StudyFlow 后端（FastAPI + Uvicorn）。

.DESCRIPTION
    后端会从 qoder/.env 读取配置（PowerContext 地址、令牌、AI 模型服务），
    并在 http://<host>:<port> 同时提供 JSON API 与前端页面。

.EXAMPLE
    powershell -NoProfile -File qoder/scripts/run_backend.ps1
    powershell -NoProfile -File qoder/scripts/run_backend.ps1 -Port 8080
#>

[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$BindAddress = '127.0.0.1',
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$qoderRoot = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent $qoderRoot
$backendDir = Join-Path $qoderRoot 'backend'

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Write-Error '找不到 Python 解释器。请先执行 uv sync。'
}

$envFile = Join-Path $qoderRoot '.env'
if (-not (Test-Path $envFile)) {
    Write-Warning "未找到 $envFile，将只使用当前进程的环境变量。"
}

Write-Host "StudyFlow 后端即将启动" -ForegroundColor Cyan
Write-Host "  地址    : http://${BindAddress}:${Port}"
Write-Host "  API 文档: http://${BindAddress}:${Port}/docs"
Write-Host ''

Push-Location $backendDir
try {
    $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', $BindAddress, '--port', "$Port")
    if ($Reload) {
        $arguments += '--reload'
    }
    & $python @arguments
}
finally {
    Pop-Location
}
