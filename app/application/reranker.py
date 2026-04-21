"""
Heuristic Reranker — 메타데이터 기반 재정렬.

RRF 1차 결과 상위 N개를 대상으로:
  - 쿼리 토큰이 title/agent_name/summary/problem에 포함 → 점수 부스트
  - category/tech_stack 매칭 → 추가 부스트
  - 원점수 × (1 + boost) 로 최종 점수 결정 → 원랭킹이 완전히 역전되지 않음

LLM 불필요, 레이턴시 거의 없음.
"""
import dataclasses
import json
import re
from app.domain.models import SearchResult

# 필드별 부스트 가중치
_W_TITLE    = 0.40  # 제목 / agent_name 포함
_W_SUMMARY  = 0.20  # one_line_summary 포함
_W_PROBLEM  = 0.15  # problem 필드 포함
_W_TECH     = 0.15  # tech_stack 매칭
_W_CATEGORY = 0.10  # category 매칭

_MAX_BOOST  = 1.20  # 최대 부스트 (원점수의 최대 2.2배까지)
_MIN_RESULTS = 3    # 재정렬 후 최소 결과 보장


def rerank(
    query: str,
    results: list[SearchResult],
    docs_meta: list[dict],
    top_n: int = 20,
) -> list[SearchResult]:
    """
    상위 top_n개를 메타데이터 기반으로 재정렬.
    top_n 초과분은 점수 변경 없이 그대로 뒤에 붙임.

    결과가 _MIN_RESULTS 미만이면 재정렬 없이 원본 반환 (필터 과도 방지).
    """
    if len(results) < _MIN_RESULTS:
        return results

    candidates = results[:top_n]
    tail = results[top_n:]

    meta_by_id = {d["id"]: d for d in docs_meta}
    tokens = _tokenize(query)

    rescored: list[tuple[SearchResult, float]] = []
    for r in candidates:
        meta = meta_by_id.get(r.document_id, {})
        boost, reason = _boost_and_reason(tokens, r, meta)
        new_score = r.score * (1.0 + min(boost, _MAX_BOOST))
        updated = dataclasses.replace(r, score=new_score, match_reason=reason)
        rescored.append((updated, new_score))

    rescored.sort(key=lambda x: -x[1])
    return [r for r, _ in rescored] + tail


def _tokenize(text: str) -> list[str]:
    """공백/특수문자 기준 분리, 2자 이상만."""
    return [t for t in re.split(r"[\s\-_/·]+", text.strip()) if len(t) >= 2]


def _parse_list(value) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return [str(v).lower() for v in parsed if v] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _boost_and_reason(
    tokens: list[str],
    result: SearchResult,
    meta: dict,
) -> tuple[float, str]:
    """부스트 점수와 매칭 근거 문자열 반환."""
    boost = 0.0
    matched: list[str] = []

    title_text  = f"{result.title} {result.agent_name or ''}".lower()
    summary_text = (result.one_line_summary or "").lower()
    problem_text = (meta.get("problem") or "").lower()
    tech_stacks  = _parse_list(meta.get("tech_stack_json"))
    category     = (meta.get("category") or "").lower()

    for token in tokens:
        t = token.lower()
        if t in title_text:
            boost += _W_TITLE
            if "제목" not in matched:
                matched.append("제목")
        if t in summary_text:
            boost += _W_SUMMARY
            if "요약" not in matched:
                matched.append("요약")
        if t in problem_text:
            boost += _W_PROBLEM
            if "문제" not in matched:
                matched.append("문제")
        if tech_stacks and any(t in ts for ts in tech_stacks):
            boost += _W_TECH
            if "기술스택" not in matched:
                matched.append("기술스택")
        if category and t in category:
            boost += _W_CATEGORY
            if "카테고리" not in matched:
                matched.append("카테고리")

    if matched:
        kws = ", ".join(f"'{t}'" for t in tokens[:2])
        reason = f"{kws} — {', '.join(matched)} 매칭"
    else:
        reason = "본문 유사도 기반"

    return boost, reason
