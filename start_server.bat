@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title G3 QWEN TTS Server

set "PYTHON_EXE=X:\KI\anaconda3\envs\qwen-tts-gui\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python Conda environment 'qwen-tts-gui' not found.
  echo Please assure Anaconda is installed there.
  echo.
  pause
  exit /b 1
)

set PYTHONUNBUFFERED=1
set QWEN_TTS_RUNTIME_BACKEND=qwen
set QWEN_TTS_MODELS_ROOT_DIR=%~dp0models
set QWEN_TTS_ALLOW_MODEL_DOWNLOADS=false
set QWEN_TTS_ADMIN_API_KEY=mein-geheimer-key-1234

REM --- Temporary Startup Admin Key ---
if not defined QWEN_TTS_STARTUP_ADMIN_KEY_TTL_SECONDS set "QWEN_TTS_STARTUP_ADMIN_KEY_TTL_SECONDS=300"
if not defined QWEN_TTS_STARTUP_ADMIN_KEY_DISPLAY_SECONDS set "QWEN_TTS_STARTUP_ADMIN_KEY_DISPLAY_SECONDS=15"
set "QWEN_TTS_STARTUP_ADMIN_KEY="
set "TMP_DIR=%~dp0.tmp"
set "STARTUP_ADMIN_KEY_FILE=%TMP_DIR%\startup_admin_key.txt"
if not exist "%TMP_DIR%" mkdir "%TMP_DIR%" > nul 2>&1
del /q "%STARTUP_ADMIN_KEY_FILE%" > nul 2>&1
"%PYTHON_EXE%" "%~dp0tools\generate_startup_admin_key.py" > "%STARTUP_ADMIN_KEY_FILE%" 2>nul
if exist "%STARTUP_ADMIN_KEY_FILE%" (
  set /p QWEN_TTS_STARTUP_ADMIN_KEY=<"%STARTUP_ADMIN_KEY_FILE%"
  del /q "%STARTUP_ADMIN_KEY_FILE%" > nul 2>&1
)

if defined QWEN_TTS_STARTUP_ADMIN_KEY (
  echo.
  echo ============================================================
  echo Temporary startup admin key ^(valid for %QWEN_TTS_STARTUP_ADMIN_KEY_TTL_SECONDS% seconds after server start^):
  echo %QWEN_TTS_STARTUP_ADMIN_KEY%
  echo Copy it now if you need emergency admin access in the browser.
  echo This screen clears automatically in %QWEN_TTS_STARTUP_ADMIN_KEY_DISPLAY_SECONDS% seconds...
  echo ============================================================
  timeout /t %QWEN_TTS_STARTUP_ADMIN_KEY_DISPLAY_SECONDS% /nobreak > nul
  cls
) else (
  echo [WARN] Temporary startup admin key could not be generated.
)

echo Starting Qwen TTS Server from %CD%...
echo Models: %QWEN_TTS_MODELS_ROOT_DIR%
if exist "%~dp0frontend\dist\index.html" (
  echo Dashboard: http://127.0.0.1:8088
) else (
  echo Frontend build missing. Run install.bat to generate frontend\dist.
  echo API: http://127.0.0.1:8088
)
"%PYTHON_EXE%" -u -m qwen_tts_server.main
pause
