@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ============================================================
echo  AI리그 로컬 탐색기 — Windows EXE 빌드
echo ============================================================

:: 가상환경 활성화
if not exist .venv\Scripts\activate.bat (
    echo [오류] 가상환경 없음. setup.bat 을 먼저 실행하세요.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

:: 빌드 전 정리
if exist dist\AI리그로컬탐색기 rmdir /s /q dist\AI리그로컬탐색기
if exist build rmdir /s /q build

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
  --hidden-import tiktoken ^
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
echo [3/3] 실행 스크립트 생성 중...
:: launcher.py가 포트 자동 탐색 + 브라우저 자동 실행 모두 처리함
(
echo @echo off
echo chcp 65001 ^> nul
echo cd /d "%%~dp0"
echo echo AI리그 로컬 탐색기를 시작합니다...
echo echo 잠시 후 브라우저가 자동으로 열립니다.
echo echo 앱을 종료하려면 이 창을 닫으세요.
echo "AI리그로컬탐색기\AI리그로컬탐색기.exe"
) > dist\실행.bat

echo.
echo ============================================================
echo  빌드 완료!
echo  배포 폴더: dist\
echo    - AI리그로컬탐색기\   (앱 본체)
echo    - 실행.bat             (더블클릭으로 실행 / 브라우저 자동 열림)
echo  dist\ 폴더 전체를 팀원에게 전달하세요.
echo ============================================================
pause
