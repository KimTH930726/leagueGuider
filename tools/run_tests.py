"""테스트 실행 헬퍼 — UTF-8 출력 보장."""
import sys
import io
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# test_self_validate 결과 수집
import importlib.util
spec = importlib.util.spec_from_file_location("tsv", "tests/test_self_validate.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

total = len(mod.results)
passed = sum(1 for _, ok, _ in mod.results if ok)
failed_list = [(n, err) for n, ok, err in mod.results if not ok]

print(f"\n{'='*60}")
print(f"  자가 검증 결과: {passed}/{total} 통과")
print(f"{'='*60}\n")

for n, err in failed_list:
    print(f"  [FAIL] {n}")
    for line in err.strip().splitlines()[:5]:
        print(f"         {line}")
    print()

if not failed_list:
    print("  [PASS] 모든 테스트 통과")
else:
    print(f"  {len(failed_list)}개 실패")

print(f"{'='*60}\n")
sys.exit(0 if not failed_list else 1)
