@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ====================================================
echo  [운영모드] AI리그 로컬 탐색기
echo  - 핫리로드 비활성화 (fileWatcherType=none)
echo  - 빈 포트 자동 탐색 (8502~)
echo  - 개발 중 코드 수정에는 run_dev.bat 을 사용하세요
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

.venv\Scripts\python.exe _start.py

pause
