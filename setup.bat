@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ============================================================
echo  AI리그 로컬 탐색기 — 초기 설정 (최초 1회만 실행)
echo ============================================================
echo.

:: 1. 가상환경 생성
echo [1/4] Python 가상환경 생성 중...
if not exist .venv\Scripts\activate.bat (
    python -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경 생성 실패. Python 3.11+ 설치 여부를 확인하세요.
        pause
        exit /b 1
    )
    echo   완료
) else (
    echo   이미 존재합니다. 스킵.
)

call .venv\Scripts\activate.bat

:: 2. 패키지 설치
echo.
echo [2/4] 패키지 설치 중... (수분 소요될 수 있습니다)
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [오류] 패키지 설치 실패.
    pause
    exit /b 1
)
echo   완료

:: 3. 로컬 임베딩 모델 다운로드
echo.
echo [3/4] 로컬 임베딩 모델 다운로드 중...
echo   모델: paraphrase-multilingual-mpnet-base-v2
echo   크기: 약 420MB  (최초 1회만 다운로드)
python -c "from app.infrastructure.embedding.local_provider import download_model; download_model('paraphrase-multilingual-mpnet-base-v2', 'data/models')"
if errorlevel 1 (
    echo [오류] 모델 다운로드 실패. 인터넷 연결을 확인하세요.
    pause
    exit /b 1
)
echo   완료

:: 4. 데이터 폴더 생성
echo.
echo [4/4] 데이터 폴더 구조 확인...
if not exist data\chroma mkdir data\chroma
if not exist data\models mkdir data\models
echo   완료

echo.
echo ============================================================
echo  설정 완료!
echo  이제 run.bat 을 실행하면 앱이 시작됩니다.
echo ============================================================
pause
