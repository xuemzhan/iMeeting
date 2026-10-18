param(
  [string]$BaseUrl = "http://127.0.0.1:8010",
  [string]$Username = "admin",
  [string]$Password = "admin123",
  [int]$TimeoutSeconds = 300
)

$login = Invoke-RestMethod "$BaseUrl/api/v1/auth/login" -Method Post `
  -ContentType "application/json" `
  -Body (@{ username = $Username; password = $Password } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }

Invoke-RestMethod "$BaseUrl/api/v1/admin/maintenance/drain" -Method Post `
  -Headers $headers -ContentType "application/json" `
  -Body (@{ reason = "graceful release" } | ConvertTo-Json) | Out-Null

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
  $state = Invoke-RestMethod "$BaseUrl/api/v1/admin/maintenance" -Headers $headers
  Write-Host "active_sessions=$($state.active_sessions)"
  if ($state.active_sessions -eq 0) { break }
  Start-Sleep -Seconds 3
}
if ($state.active_sessions -ne 0) {
  throw "仍有活跃会话，已停止发布流程。确认用户收到通知后再重试。"
}

Write-Host "排空完成，可以安全停止进程并启动新版本。"
