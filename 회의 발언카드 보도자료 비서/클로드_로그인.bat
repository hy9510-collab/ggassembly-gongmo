@echo off
chcp 949 >nul
set LOG=%~dp0로그인_로그.txt
echo [%DATE% %TIME%] ===== 시작 ===== > "%LOG%"
title Claude Code 로그인 (최초 1회)
echo ==============================================
echo   Claude Code 로그인 - 최초 1회만 하면 됩니다
echo ==============================================
echo.
echo  1. 잠시 후 Claude 화면이 열립니다
echo  2. /login 을 입력하고 엔터
echo  3. 브라우저가 열리면 로그인/승인
echo  4. 완료되면 이 창을 닫으세요
echo.
pause
set CLAUDEDIR=
rem ── 방법 1: 스토어 앱 실제 저장 경로 (명령창에서 보이는 위치)
for /d %%v in ("%LOCALAPPDATA%\Packages\Claude_*") do (
  for /d %%w in ("%%v\LocalCache\Roaming\Claude\claude-code\*") do set CLAUDEDIR=%%w
)
echo [%DATE% %TIME%] 방법1(Packages) CLAUDEDIR=[%CLAUDEDIR%] >> "%LOG%"
rem ── 방법 2: 일반 설치 경로
if not defined CLAUDEDIR (
  for /d %%v in ("%APPDATA%\Claude\claude-code\*") do set CLAUDEDIR=%%v
)
echo [%DATE% %TIME%] 방법2(APPDATA) 후 CLAUDEDIR=[%CLAUDEDIR%] >> "%LOG%"
if not defined CLAUDEDIR (
  echo.
  echo [오류] Claude 프로그램을 찾지 못했습니다.
  echo   Claude 채팅에 "로그 확인해줘"라고 말씀해 주세요.
  dir /b "%LOCALAPPDATA%\Packages" >> "%LOG%" 2>&1
  echo.
  pause
  exit /b 1
)
echo 실행 파일 위치: %CLAUDEDIR%\claude.exe
echo [%DATE% %TIME%] claude.exe 실행: %CLAUDEDIR% >> "%LOG%"
echo.
"%CLAUDEDIR%\claude.exe"
echo [%DATE% %TIME%] claude.exe 종료 (코드 %errorlevel%) >> "%LOG%"
echo.
echo Claude 창을 닫으셨네요. 이 창도 닫으셔도 됩니다.
pause
