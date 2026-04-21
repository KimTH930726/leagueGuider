import sqlite3, json, sys

conn = sqlite3.connect("data/league_guider.db")
conn.row_factory = sqlite3.Row

total    = conn.execute("SELECT COUNT(*) FROM documents WHERE is_deleted=0").fetchone()[0]
meta     = conn.execute("SELECT COUNT(*) FROM document_metadata").fetchone()[0]
tech_cnt = conn.execute("SELECT COUNT(*) FROM document_metadata WHERE tech_stack_json NOT IN ('null','[]','') AND tech_stack_json IS NOT NULL").fetchone()[0]
cat_cnt  = conn.execute("SELECT COUNT(*) FROM document_metadata WHERE category IS NOT NULL AND category != '' AND category != '기타'").fetchone()[0]
kw_cnt   = conn.execute("SELECT COUNT(*) FROM document_metadata WHERE keywords_json NOT IN ('null','[]','') AND keywords_json IS NOT NULL").fetchone()[0]
only_etc = conn.execute("SELECT COUNT(*) FROM document_metadata WHERE category='기타'").fetchone()[0]

cats = conn.execute("SELECT category, COUNT(*) cnt FROM document_metadata WHERE category IS NOT NULL AND category!='' GROUP BY category ORDER BY cnt DESC LIMIT 10").fetchall()

# LLM 설정 확인
try:
    settings = conn.execute("SELECT llm_provider, llm_model, extract_metadata FROM app_settings WHERE id=1").fetchone()
    llm_info = dict(settings) if settings else {}
except Exception:
    llm_info = {}

# 샘플 one_line_summary 확인
has_summary = conn.execute("SELECT COUNT(*) FROM document_metadata WHERE one_line_summary IS NOT NULL AND one_line_summary!=''").fetchone()[0]

output = [
    "=== DB 현황 ===",
    f"전체 문서     : {total}건",
    f"메타 추출 완료: {meta}건  ({round(meta/max(total,1)*100)}%)",
    f"기술스택 보유 : {tech_cnt}건  ({round(tech_cnt/max(total,1)*100)}%)",
    f"키워드 보유   : {kw_cnt}건  ({round(kw_cnt/max(total,1)*100)}%)",
    f"요약 보유     : {has_summary}건",
    f"카테고리(기타제외): {cat_cnt}건",
    f"기타 카테고리 : {only_etc}건  ← LLM 미추출 fallback",
    "",
    "=== 카테고리 분포 ===",
]
for r in cats:
    output.append(f"  {r['category']}: {r['cnt']}건")

output += [
    "",
    "=== LLM 설정 (app_settings) ===",
    f"  {llm_info}",
]

sys.stdout.buffer.write(("\n".join(output) + "\n").encode("utf-8"))
conn.close()
