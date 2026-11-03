# iMeeting · SQLite 恢复（Windows / PowerShell）
#
# 与 deploy/restore_sqlite.sh 等价：先校验备份、停后端、恢复、再起后端等 /ready。
# 真正的校验/安全副本/原子替换在 `python -m imeeting.sqlite_backup`（IM-PLN-004 §6.1）。

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BackupName,
    [string]$BackupDir = "/data/backups",
    [string]$Current = "/data/meeting.db",
    [string]$ComposeFile = "deploy/compose.yaml",
    [string]$EnvFile = "deploy/production.env",
    [int]$ReadyAttempts = 90
)

$ErrorActionPreference = "Stop"

if ($env:CONFIRM_RESTORE -ne "yes") {
    Write-Error "Refusing to restore without CONFIRM_RESTORE=yes（`$env:CONFIRM_RESTORE='yes' 后再执行）"
}

# 只接受备份文件名：拒绝路径穿越与绝对路径，与 sh 版本保持一致
if ($BackupName -match '[\\/]' -or $BackupName -match '\.\.') {
    Write-Error "Usage: `$env:CONFIRM_RESTORE='yes'; .\deploy\restore_sqlite.ps1 <backup-file-name.db>"
}

$composeArgs = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)

# 1) 校验备份可读
& docker @composeArgs run --rm --no-deps --entrypoint python backend `
    -m imeeting.sqlite_backup verify --path "$BackupDir/$BackupName"
if ($LASTEXITCODE -ne 0) { Write-Error "备份文件校验失败：$BackupName" }

# 2) 停后端，避免恢复期间仍在写库
& docker @composeArgs stop backend
if ($LASTEXITCODE -ne 0) { Write-Error "停止后端失败" }

try {
    # 3) 恢复（内部会先留安全副本再原子替换）
    & docker @composeArgs run --rm --no-deps --entrypoint python backend `
        -m imeeting.sqlite_backup restore `
        --current $Current --backup "$BackupDir/$BackupName" --backup-dir $BackupDir
    if ($LASTEXITCODE -ne 0) { Write-Error "恢复失败" }
}
finally {
    # 4) 无论恢复成败都要把后端拉起来，否则运维把服务留在停摆状态
    & docker @composeArgs up -d backend
    if ($LASTEXITCODE -ne 0) { Write-Error "恢复后重启后端失败" }

    $ready = $false
    for ($i = 0; $i -lt $ReadyAttempts; $i++) {
        & docker @composeArgs exec -T backend `
            curl -fsS http://127.0.0.1:8000/api/v1/ready *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { Write-Error "恢复后后端未就绪（已尝试 $ReadyAttempts 次）" }
    Write-Host "恢复完成：$BackupName"
}
