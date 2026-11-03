# iMeeting · SQLite 备份（Windows / PowerShell）
#
# 与 deploy/backup_sqlite.sh 等价：两者都只是 `python -m imeeting.sqlite_backup`
# 的薄包装，真正的校验/安全副本/原子替换逻辑在 Python 里（IM-PLN-004 §6.1）。
# 之所以要坚持"薄"，是因为这套逻辑写两遍必然漂移。

[CmdletBinding()]
param(
    # 备份文件落盘目录（容器内路径，默认与 compose 的数据卷一致）
    [string]$BackupDir = "/data/backups",
    [string]$Source = "/data/meeting.db",
    [string]$ComposeFile = "deploy/compose.yaml",
    [string]$EnvFile = "deploy/production.env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $EnvFile)) {
    # compose 文件里引用了 env_file，缺失时给出明确提示而不是 docker 的原文报错
    Write-Error "缺少环境文件 $EnvFile（可用 -EnvFile 指定）"
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$target = "$BackupDir/meeting-$stamp.db"

& docker compose --env-file $EnvFile -f $ComposeFile exec -T backend `
    python -m imeeting.sqlite_backup backup --source $Source --target $target

if ($LASTEXITCODE -ne 0) {
    Write-Error "备份失败（退出码 $LASTEXITCODE）"
}

Write-Host "备份完成：$target"
