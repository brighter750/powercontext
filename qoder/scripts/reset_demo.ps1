<#
.SYNOPSIS
    清空 StudyFlow 的本地演示状态（PowerContext 数据库 + 运行产物）。

.DESCRIPTION
    用于把演示环境恢复到干净状态：删除 StudyFlow 专用的 PowerContext SQLite 数据库
    及其 WAL / SHM / 迁移状态文件，并清理后端日志。

    脚本只删除 qoder/.artifacts/ 下的文件，不会触碰本机其它 PowerContext 实例的数据。

.EXAMPLE
    powershell -NoProfile -File qoder/scripts/reset_demo.ps1
    powershell -NoProfile -File qoder/scripts/reset_demo.ps1 -KeepLogs
#>

[CmdletBinding()]
param(
    [switch]$KeepLogs
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$qoderRoot = Split-Path -Parent $scriptDir
$artifactDir = Join-Path $qoderRoot '.artifacts'

if (-not (Test-Path $artifactDir)) {
    Write-Host '没有需要清理的演示状态。'
    return
}

$targets = Get-ChildItem -Path $artifactDir -Force | Where-Object {
    $_.Name -like 'powercontext-studyflow.db*' -or
    $_.Name -like '.powercontext-studyflow.db*'
}

if (-not $targets) {
    Write-Host '没有找到 StudyFlow 专用的 PowerContext 数据库，无需清理。'
}
else {
    foreach ($target in $targets) {
        Write-Host "删除 $($target.Name)"
        if ($target.PSIsContainer) {
            Remove-Item -LiteralPath $target.FullName -Recurse -Force
        }
        else {
            Remove-Item -LiteralPath $target.FullName -Force
        }
    }
}

if (-not $KeepLogs) {
    Get-ChildItem -Path $artifactDir -Filter '*.log' -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "删除 $($_.Name)"
        Remove-Item -LiteralPath $_.FullName -Force
    }
}

Write-Host ''
Write-Host '演示状态已重置。重新启动 PowerContext Server 后运行 scripts/seed_demo.py 即可恢复演示数据。' -ForegroundColor Green
