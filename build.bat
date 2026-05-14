@echo off
chcp 949 > nul
cd /d "%~dp0"

echo ============================================================
echo  AI리그 로컬 탐색기 - Windows EXE 빌드
echo ============================================================

:: 가상환경 활성화
if not exist .venv\Scripts\activate.bat (
    echo [오류] 가상환경 없음. setup.bat 을 먼저 실행하세요.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

:: 빌드 전 정리
if exist dist\AI리그로컬탐색기 rd /s /q dist\AI리그로컬탐색기
if exist build rd /s /q build

:: data/models 폴더 없으면 생성 (--add-data 오류 방지)
if not exist data\models mkdir data\models

echo.
echo [1/3] PyInstaller 빌드 중... (수분 소요)

pyinstaller ^
  --onedir ^
  --name "AI리그로컬탐색기" ^
  --add-data "main.py;." ^
  --add-data "config;config" ^
  --add-data "app;app" ^
  --add-data "data\models;data\models" ^
  --hidden-import streamlit ^
  --hidden-import streamlit.web.cli ^
  --hidden-import streamlit.runtime ^
  --hidden-import chromadb ^
  --hidden-import chromadb.api.segment ^
  --hidden-import openai ^
  --hidden-import sentence_transformers ^
  --hidden-import torch ^
  --hidden-import transformers ^
  --hidden-import keyring ^
  --hidden-import keyring.backends ^
  --hidden-import keyring.backends.Windows ^
  --hidden-import keyring.backends.fail ^
  --collect-all streamlit ^
  --collect-all chromadb ^
  --collect-all keyring ^
  launcher.py

if errorlevel 1 (
    echo [오류] 빌드 실패.
    pause
    exit /b 1
)

echo.
echo [2/3] 런타임 데이터 폴더 구성 중...
if not exist dist\AI리그로컬탐색기\data\chroma mkdir dist\AI리그로컬탐색기\data\chroma
if not exist dist\AI리그로컬탐색기\data\models mkdir dist\AI리그로컬탐색기\data\models

echo.
echo [3/3] 실행 스크립트 배치 중...
:: heredoc은 cmd 활성 코드페이지로 기록돼 깨질 수 있어 미리 만든 CP949 템플릿을 복사한다.
copy /y "scripts\dist_run.template.bat" "dist\실행.bat" > nul
if errorlevel 1 (
    echo [오류] dist\실행.bat 배치 실패. scripts\dist_run.template.bat 존재 여부를 확인하세요.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  빌드 완료!
echo  배포 폴더: dist\
echo    - AI리그로컬탐색기\   (앱 본체)
echo    - 실행.bat             (더블클릭으로 실행)
echo  dist\ 폴더 전체를 팀원에게 전달하세요.
echo ============================================================
pause
