"""청크 분할 (명세서 6장 ③, AI 담당).

수집기(sources 앱, B 담당)는 정제한 본문을 prepare_chunks()에 넣고,
돌려받은 청크를 embed_passages()로 임베딩해서 source_chunk에 저장하면 된다.
"""
import re
from datetime import date

from .config import get_settings

# 제목(#)이나 조문("제5조") 앞에서 끊음
_SPLIT_RE = re.compile(r"(?m)^(?=#{1,6}\s|제\s*\d+\s*조)")


def split_chunks(body: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    s = get_settings()
    size = size or s.chunk_size
    overlap = s.chunk_overlap if overlap is None else overlap
    # 다음 조각 시작 위치가 size - overlap만큼 앞으로 가야 함. 0 이하면 긴 섹션에서 무한 루프
    if overlap >= size:
        raise ValueError(f"청크 overlap({overlap})은 size({size})보다 작아야 해 (AI_CHUNK_OVERLAP < AI_CHUNK_SIZE)")
    raw = [x.strip() for x in _SPLIT_RE.split(body) if x.strip()]

    # 짧은 섹션("# 문의" 한 줄 등)은 다음 섹션과 합침. 짧은 청크는 아무 질문에나 점수가 높게 나와서 검색을 망침.
    sections: list[str] = []
    buf = ""
    for sec in raw:
        buf = f"{buf}\n\n{sec}" if buf else sec
        if len(buf) >= s.min_chunk_chars:
            sections.append(buf)
            buf = ""
    if buf:
        if sections and len(sections[-1]) + len(buf) <= size:
            sections[-1] += "\n\n" + buf
        else:
            sections.append(buf)

    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= size:
            chunks.append(sec)
            continue
        # 긴 섹션은 섹션 제목을 각 조각 앞에 붙여서 자름
        first = sec.splitlines()[0]
        heading = first if first.startswith(("#", "제")) else ""
        start = 0
        while start < len(sec):
            piece = sec[start : start + size]
            if heading and start > 0:
                piece = f"{heading}\n{piece}"
            chunks.append(piece.strip())
            start += size - overlap
    return chunks


def prepare_chunks(body: str, title: str | None = None) -> list[str]:
    """수집기가 호출하는 함수. 청크마다 자료 제목을 붙여서 어떤 자료인지 검색에 걸리게 한다."""
    title = (title or "").replace("[샘플]", "").strip()
    return [f"[{title}]\n{c}" if title else c for c in split_chunks(body)]


def parse_front_matter(text: str) -> tuple[dict, str]:
    """.md 자료 파일 머리말(--- key: value ---) 파싱. 메모리 검색기에서만 씀."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[3:end].strip().splitlines():
        line = line.split(" #", 1)[0].strip()
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[end + 4 :].lstrip("\n")


def parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None
