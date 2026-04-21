"""텍스트 처리 유틸 — 외부 의존성 없음."""
import re
from datetime import datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """KST 현재 시각 반환."""
    return datetime.now(_KST)


def now_kst_str() -> str:
    """KST 현재 시각을 'YYYY-MM-DDTHH:MM:SS' 형식으로 반환."""
    return datetime.now(_KST).strftime("%Y-%m-%dT%H:%M:%S")

# 숫자 헤딩(1. / 4-1.) 또는 마크다운 헤딩(## / ###) 감지
_HEADING_RE = re.compile(r'(?m)^(?:#{1,4}\s+|\d+(?:-\d+)?\.\s+\S)')


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[dict]:
    """
    헤딩 인식 우선 청킹.
    숫자 헤딩(1. / 4-1.) 또는 마크다운 헤딩(##) 이 2개 이상이면 섹션 단위로 청킹.
    헤딩 없는 문서는 문단(\\n\\n) 기반 청킹으로 폴백.
    """
    if not text:
        return []

    sections = _split_by_headings(text)
    if len(sections) >= 2:
        return _chunk_from_sections(sections, chunk_size)
    return _chunk_by_paragraphs(text, chunk_size, overlap)


# ── 헤딩 기반 ──────────────────────────────────────────────────────────────

def _split_by_headings(text: str) -> list[str]:
    """헤딩 위치를 기준으로 텍스트를 섹션 리스트로 분리."""
    boundaries = [m.start() for m in _HEADING_RE.finditer(text)]
    if len(boundaries) < 2:
        return []

    result: list[str] = []

    # 첫 헤딩 이전 preamble (개요 테이블 등)
    if boundaries[0] > 0:
        preamble = text[: boundaries[0]].strip()
        if preamble:
            result.append(preamble)

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        section = text[start:end].strip()
        if section:
            result.append(section)

    return result


def _chunk_from_sections(sections: list[str], chunk_size: int) -> list[dict]:
    """섹션 리스트를 chunk_size 기준으로 병합·분할."""
    chunks: list[dict] = []
    idx = 0
    current = ""

    for section in sections:
        if len(section) > chunk_size:
            # 누적 중인 chunk 먼저 저장
            if current.strip():
                chunks.append(_make_chunk(idx, current))
                idx += 1
                current = ""
            # 긴 섹션은 문단 기반으로 재분할 (헤딩이 컨텍스트 제공하므로 overlap 불필요)
            for sub in _chunk_by_paragraphs(section, chunk_size, overlap=0):
                sub["chunk_index"] = idx
                chunks.append(sub)
                idx += 1
        elif current and len(current) + len(section) + 2 > chunk_size:
            chunks.append(_make_chunk(idx, current))
            idx += 1
            current = section
        else:
            current = (current + "\n\n" + section).strip() if current else section

    if current.strip():
        chunks.append(_make_chunk(idx, current))

    return chunks


# ── 문단 기반 (폴백) ────────────────────────────────────────────────────────

def _chunk_by_paragraphs(text: str, chunk_size: int, overlap: int) -> list[dict]:
    """\\n\\n 문단 분리 후 chunk_size 이하로 그룹화. 마지막 문단 단위 overlap."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[dict] = []
    idx = 0
    current = ""

    for para in paragraphs:
        if len(para) > chunk_size:
            if current:
                chunks.append(_make_chunk(idx, current))
                idx += 1
                current = ""
            for i in range(0, len(para), chunk_size - max(overlap, 1)):
                piece = para[i: i + chunk_size].strip()
                if piece:
                    chunks.append(_make_chunk(idx, piece))
                    idx += 1
            continue

        candidate = (current + "\n\n" + para).strip() if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(_make_chunk(idx, current))
                idx += 1
            last_para = current.split("\n\n")[-1].strip() if current and overlap > 0 else ""
            current = (last_para + "\n\n" + para).strip() if last_para else para

    if current and current.strip():
        chunks.append(_make_chunk(idx, current))

    return chunks


def _make_chunk(idx: int, text: str) -> dict:
    t = text.strip()
    return {"chunk_index": idx, "chunk_text": t, "token_count": len(t) // 4}
