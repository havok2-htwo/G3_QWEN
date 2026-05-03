@echo off
setlocal
cd /d "%~dp0frontend"
title G3 QWEN TTS Frontend

echo Starting Qwen TTS Frontend from %CD%...
echo Open: http://127.0.0.1:5178
npm run dev -- --host 127.0.0.1 --port 5178 --clearScreen false
pause
