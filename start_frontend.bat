@echo off
setlocal
cd /d "%~dp0frontend"
title G3 QWEN TTS Control Room

echo Starting Qwen TTS Frontend from %CD%...
echo Open: http://127.0.0.1:5178
call .\node_modules\.bin\vite.cmd --host 127.0.0.1 --port 5178 --clearScreen false
pause
