#!/bin/bash
# AI리그 로컬 탐색기 — macOS 빌드
set -e

echo "============================================================"
echo " AI리그 로컬 탐색기 — macOS 빌드"
echo "============================================================"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 가상환경 확인
if [ ! -f ".venv/bin/activate" ]; then
    echo "[오류] 가상환경 없음. 먼저 실행하세요:"
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
source .venv/bin/activate

# 빌드 전 정리
rm -rf dist/AI리그로컬탐색기 build

# data/models 폴더 없으면 생성 (--add-data 오류 방지)
mkdir -p data/models

echo ""
echo "[1/3] PyInstaller 빌드 중... (수분 소요)"

# macOS: --add-data 구분자는 콜론(:) 사용
pyinstaller \
  --onedir \
  --name "AI리그로컬탐색기" \
  --add-data "config:config" \
  --add-data "app:app" \
  --add-data "data/models:data/models" \
  --hidden-import streamlit \
  --hidden-import streamlit.web.cli \
  --hidden-import streamlit.runtime \
  --hidden-import chromadb \
  --hidden-import chromadb.api.segment \
  --hidden-import openai \
  --hidden-import tiktoken \
  --hidden-import sentence_transformers \
  --hidden-import torch \
  --hidden-import transformers \
  --collect-all streamlit \
  --collect-all chromadb \
  launcher.py

echo ""
echo "[2/3] 런타임 데이터 폴더 구성 중..."
mkdir -p dist/AI리그로컬탐색기/data/chroma
mkdir -p dist/AI리그로컬탐색기/data/models

echo ""
echo "[3/3] 실행 스크립트 생성 중..."
# launcher.py가 포트 자동 탐색 + 브라우저 자동 실행 모두 처리함
cat > dist/실행.sh << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
echo "AI리그 로컬 탐색기를 시작합니다..."
echo "잠시 후 브라우저가 자동으로 열립니다."
./AI리그로컬탐색기/AI리그로컬탐색기
EOF
chmod +x dist/실행.sh

echo ""
echo "============================================================"
echo " 빌드 완료!"
echo " 배포 폴더: dist/"
echo "   - AI리그로컬탐색기/  (앱 본체)"
echo "   - 실행.sh             (더블클릭 또는 터미널에서 실행)"
echo " dist/ 폴더 전체를 팀원에게 전달하세요."
echo "============================================================"
