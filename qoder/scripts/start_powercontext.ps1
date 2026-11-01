<#
.SYNOPSIS
    启动一个供 StudyFlow 使用的 PowerContext Server（本地 SQLite + 强制鉴权）。

.DESCRIPTION
    该脚本使用仓库内已安装的 powercontext CLI 启动一个独立的 PowerContext Server：
      * 使用独立数据库文件，避免与本机其它演示互相干扰；
      * 使用 ACCESS_MODE=enforced + AUTH_TOKEN，使客户端必须携带 Bearer 凭据；
      * 监听 127.0.0.1:<端口>，不暴露到局域网。

    脚本不会打印任何真实密钥；令牌来自环境变量或本地 .env。

.EXAMPLE
    powershell -NoProfile -File qoder/scripts/start_powercontext.ps1
    powershell -NoProfile -File qoder/scripts/start_powercontext.ps1 -Port 8765 -Token "your-token"
#>

[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$BindAddress = '127.0.0.1',
    [string]$DatabasePath = '',
    [string]$Token = $env:STUDYFLOW_POWERCONTEXT_SERVER_TOKEN,
    [switch]$OpenDashboard
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$qoderRoot = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent $qoderRoot

if (-not $DatabasePath) {
    $artifactDir = Join-Path $qoderRoot '.artifacts'
    New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
    $DatabasePath = Join-Path $artifactDir 'powercontext-studyflow.db'
}

if (-not $Token) {
    Write-Error '缺少 PowerContext 服务令牌。请设置 STUDYFLOW_POWERCONTEXT_SERVER_TOKEN，或用 -Token 传入。'
}

$cli = Join-Path $repoRoot '.venv\Scripts\powercontext.exe'
if (-not (Test-Path $cli)) {
    $cli = (Get-Command powercontext -ErrorAction SilentlyContinue).Source
}
if (-not $cli) {
    Write-Error '找不到 powercontext CLI。请先执行 uv sync，或用 uv tool install 安装 powercontext[cli,server]。'
}

$databaseUrl = 'sqlite+aiosqlite:///' + ($DatabasePath -replace '\\', '/')

$env:POWERCONTEXT_SERVER_HTTP_HOST = $BindAddress
$env:POWERCONTEXT_SERVER_HTTP_PORT = "$Port"
$env:POWERCONTEXT_SERVER_DATABASE_URL = $databaseUrl
$env:POWERCONTEXT_SERVER_ACCESS_MODE = 'enforced'
$env:POWERCONTEXT_SERVER_AUTH_TOKEN = $Token
if ($OpenDashboard) {
    $env:POWERCONTEXT_SERVER_DASHBOARD_ENABLED = 'true'
}
else {
    $env:POWERCONTEXT_SERVER_DASHBOARD_ENABLED = 'false'
}

Write-Host "PowerContext Server 即将启动" -ForegroundColor Cyan
Write-Host "  地址        : http://${BindAddress}:${Port}"
Write-Host "  数据库      : $databaseUrl"
Write-Host "  访问控制    : enforced（客户端需要 Authorization: Bearer <token>）"
Write-Host "  Dashboard   : http://${BindAddress}:${Port}/dashboard/home"
Write-Host ''

& $cli server run
