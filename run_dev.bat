@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ====================================================
echo  [개발모드] AI리그 로컬 탐색기
echo  - 코드 수정 시 자동 반영 (핫리로드 활성화)
echo  - 포트: 8502 고정
echo  - 운영 배포에는 run_prod.bat 을 사용하세요
echo ====================================================

if not exist .venv\Scripts\activate.bat (
    echo 가상환경이 없습니다. 먼저 setup.bat 을 실행하세요.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

.venv\Scripts\python.exe -c "import streamlit" 2>nul
if errorlevel 1 (
    echo 패키지 설치 중...
    .venv\Scripts\pip.exe install -r requirements.txt
)

echo [개발모드] 브라우저에서 http://localhost:8502 를 여세요.
echo [개발모드] 코드를 저장하면 앱이 자동으로 새로고침됩니다.
echo.

.venv\Scripts\streamlit.exe run main.py ^
    --server.port=8502 ^
    --server.headless=false ^
    --server.fileWatcherType=watchdog ^
    --browser.gatherUsageStats=false

pause
