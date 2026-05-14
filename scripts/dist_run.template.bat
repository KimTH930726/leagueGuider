@echo off
cd /d "%~dp0"
if exist "AI리그로컬탐색기\.app_state" del /f /q "AI리그로컬탐색기\.app_state" >nul 2>&1
echo AI리그 로컬 탐색기를 시작합니다...
echo 잠시 후 브라우저가 자동으로 열립니다.
echo 앱을 종료하려면 이 창을 닫으세요.
"AI리그로컬탐색기\AI리그로컬탐색기.exe"
