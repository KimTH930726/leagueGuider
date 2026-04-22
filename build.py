"""
플랫폼 자동 감지 통합 빌드 스크립트.
Windows / macOS 모두 동일한 명령으로 실행:
    python build.py
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "AI리그로컬탐색기"
BASE_DIR = Path(__file__).parent
DEFAULT_EMBED_MODEL = "paraphrase-multilingual-mpnet-base-v2"


def _sep() -> str:
    """PyInstaller --add-data 구분자: Windows=';', macOS/Linux=':'"""
    return ";" if platform.system() == "Windows" else ":"


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[오류] 명령 실패: {' '.join(cmd)}")
        sys.exit(result.returncode)


def _download_embed_model(model_name: str) -> None:
    """
    임베딩 모델을 data/models/{model_name}/ 에 SentenceTransformer 포맷으로 저장.
    이미 존재하면 스킵. EXE 번들용 오프라인 모델 준비.
    """
    dest = Path("data/models") / model_name
    if dest.exists() and any(dest.iterdir()):
        print(f"  임베딩 모델 이미 존재 (스킵): {dest}")
        return
    print(f"  임베딩 모델 다운로드: {model_name}")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        model.save(str(dest))
        print(f"  임베딩 모델 저장 완료: {dest}")
    except Exception as e:
        print(f"  [경고] 모델 다운로드 실패 (인터넷 필요): {e}")
        print("  EXE 실행 시 첫 구동에서 자동 다운로드됩니다.")


def main() -> None:
    os.chdir(BASE_DIR)
    s = _sep()
    is_windows = platform.system() == "Windows"
    print(f"플랫폼: {platform.system()} | 구분자: '{s}'")

    # 빌드 전 정리
    for d in [f"dist/{APP_NAME}", "build"]:
        if Path(d).exists():
            shutil.rmtree(d)

    # 임베딩 모델 번들 준비 (오프라인 EXE용)
    print("\n[0/3] 임베딩 모델 준비 중...")
    _download_embed_model(DEFAULT_EMBED_MODEL)

    print("\n[1/3] PyInstaller 빌드 중... (수분 소요)")
    _run([
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "-y",
        f"--name={APP_NAME}",
        f"--add-data=main.py{s}.",
        f"--add-data=config{s}config",
        f"--add-data=app{s}app",
        f"--add-data=data/models/{DEFAULT_EMBED_MODEL}{s}data/models/{DEFAULT_EMBED_MODEL}",
        "--hidden-import=streamlit",
        "--hidden-import=streamlit.web.cli",
        "--hidden-import=streamlit.runtime",
        "--hidden-import=chromadb",
        "--hidden-import=chromadb.api.segment",
        "--hidden-import=openai",
        "--hidden-import=tiktoken",
        "--hidden-import=sentence_transformers",
        "--hidden-import=torch",
        "--hidden-import=transformers",
        "--hidden-import=keyring",
        "--hidden-import=keyring.backends",
        "--hidden-import=keyring.backends.Windows",
        "--hidden-import=keyring.backends.fail",
        "--collect-all=streamlit",
        "--collect-all=chromadb",
        "--collect-all=keyring",
        "launcher.py",
    ])

    print("\n[2/3] 런타임 데이터 폴더 구성 중...")
    Path(f"dist/{APP_NAME}/data/chroma").mkdir(parents=True, exist_ok=True)
    Path(f"dist/{APP_NAME}/data/models").mkdir(parents=True, exist_ok=True)

    print("\n[3/3] 실행 스크립트 생성 중...")
    # launcher.py가 포트 자동 탐색 + 브라우저 자동 실행 처리
    if is_windows:
        runner = Path("dist/실행.bat")
        exe_rel = f"{APP_NAME}\\{APP_NAME}.exe"
        runner.write_text(
            "@echo off\nchcp 65001 > nul\ncd /d \"%~dp0\"\n"
            "echo AI리그 로컬 탐색기를 시작합니다...\n"
            "echo 잠시 후 브라우저가 자동으로 열립니다.\n"
            "echo 앱을 종료하려면 이 창을 닫으세요.\n"
            f'"{exe_rel}"\n',
            encoding="utf-8",
        )
        print(f"  → dist\\실행.bat 생성")
    else:
        runner = Path("dist/실행.sh")
        exe_rel = f"{APP_NAME}/{APP_NAME}"
        runner.write_text(
            "#!/bin/bash\ncd \"$(dirname \"$0\")\"\n"
            "echo 'AI리그 로컬 탐색기를 시작합니다...'\n"
            "echo '잠시 후 브라우저가 자동으로 열립니다.'\n"
            f"./{exe_rel}\n",
            encoding="utf-8",
        )
        runner.chmod(0o755)
        print(f"  → dist/실행.sh 생성")

    print(f"""
============================================================
 빌드 완료!  [{platform.system()}]
 배포 폴더: dist/
   - {APP_NAME}/    (앱 본체)
   - {'실행.bat' if is_windows else '실행.sh'}  (더블클릭 또는 터미널에서 실행)
 포트 충돌 시 8502~8521 범위에서 자동으로 빈 포트 선택됩니다.
 dist/ 폴더 전체를 팀원에게 전달하세요.
============================================================""")


if __name__ == "__main__":
    main()
