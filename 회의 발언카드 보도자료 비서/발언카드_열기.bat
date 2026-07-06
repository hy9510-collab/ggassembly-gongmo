@echo off
chcp 949 >nul
title 발언카드 보도자료 비서
cd /d "%~dp0"
echo ============================================
echo    발언카드 보도자료 비서를 시작합니다
echo ============================================
echo.
echo  잠시후 창이 자동으로 열립니다.
echo  이 창은 닫지 마세요 - 닫으면 서버가 종료됩니다.
echo.
start "speechcard-server" /min .venv\Scripts\python.exe server.py 4600
timeout /t 2 >nul
start "" "http://localhost:4600/"
echo  브라우저를 열었습니다.
echo  다 쓰신 후 이 창을 닫으면 서버가 종료됩니다.
pause >nul
