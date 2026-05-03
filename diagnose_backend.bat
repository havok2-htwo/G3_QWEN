@echo off
setlocal
cd /d "%~dp0"
set API=http://127.0.0.1:8088
set KEY=mein-geheimer-key-1234

echo Running backend diagnostics against %API%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "function Check([string]$Name, [string]$Url, [bool]$Protected) {" ^
  "  Write-Host '';" ^
  "  Write-Host ('=== ' + $Name + ' ===');" ^
  "  try {" ^
  "    $headers = @{};" ^
  "    if ($Protected) { $headers['Authorization'] = 'Bearer %KEY%' }" ^
  "    $sw = [System.Diagnostics.Stopwatch]::StartNew();" ^
  "    $response = Invoke-WebRequest -Uri $Url -Headers $headers -UseBasicParsing -TimeoutSec 5;" ^
  "    $sw.Stop();" ^
  "    Write-Host ('Status: ' + [int]$response.StatusCode + ' | TimeMs: ' + $sw.ElapsedMilliseconds);" ^
  "    Write-Host $response.Content;" ^
  "  } catch {" ^
  "    $msg = if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message };" ^
  "    Write-Host ('ERROR: ' + $msg);" ^
  "  }" ^
  "}" ^
  "Check 'health' '%API%/health' $false;" ^
  "Check 'stats' '%API%/v1/stats' $true;" ^
  "Check 'models' '%API%/v1/models' $true;" ^
  "Check 'settings' '%API%/v1/settings' $true;"
echo.
pause