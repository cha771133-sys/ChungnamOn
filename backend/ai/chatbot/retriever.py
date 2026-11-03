"""공식 자료 검색 (명세서 6장, AI 담당: 검색 튜닝).

- MemoryRetriever: AI_KB_DIR의 .md를 메모리에 올려서 검색. Django 없이 개발할 때
- DbRetriever    : sources 앱 테이블 검색 (Postgres+pgvector면 SQL, 아니면 numpy)

AI_RETRIEVER=db(기본, Django 안) | memory(Django 없이 개발) 로 고른다.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

import numpy as np

from .chunker import parse_date, parse_front_matter, prepare_chunks
from .config import get_settings
from .embedder import get_embedder, to_pgvector

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    chunk_id: int
    text: str
    score: float              # 코사인 유사도
    url: str
    title: str | None
    agency: str | None
    region: str | None
    published_at: date | None  # 자료 기준일
    checked_at: date           # 서비스 확인일 (collected_at)


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return parse_date(str(v)[:10])


class MemoryRetriever:
    def __init__(self):
        s = get_settings()
        emb = get_embedder()
        self.rows: list[dict] = []
        texts: list[str] = []
        today = date.today()
        files = sorted(s.kb_dir.glob("**/*.md")) if s.kb_dir.exists() else []
        if not files:
            log.warning("%s에 .md 자료가 없어서 검색 결과가 항상 비어 있음 (테스트 샘플: ai/tests/fixtures)", s.kb_dir)
        for p in files:
            meta, body = parse_front_matter(p.read_text(encoding="utf-8"))
            for c in prepare_chunks(body, meta.get("title")):
                self.rows.append(
                    {
                        "url": meta.get("source_url", p.name),
                        "title": meta.get("title"),
                        "agency": meta.get("agency"),
                        "region": meta.get("region"),
                        "published_at": parse_date(meta.get("published_at")),
                        "checked_at": today,
                    }
                )
                texts.append(c)
        self.texts = texts
        vecs = emb.embed_passages(texts) if texts else []
        m = np.asarray(vecs, dtype=np.float32).reshape(len(texts), -1) if texts else np.zeros((0, s.embed_dim))
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        self.matrix = m / np.where(norms == 0, 1, norms)

    def search(self, query: str, *, regions: list[str] | None, top_k: int, min_score: float) -> list[SearchResult]:
        if not self.texts:
            return []
        q = np.asarray(get_embedder().embed_query(query), dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1)
        scores = self.matrix @ q
        out = []
        for i in np.argsort(-scores):
            r = self.rows[i]
            if regions and r["region"] not in regions:
                continue
            if scores[i] < min_score:
                break
            out.append(SearchResult(int(i) + 1, self.texts[i], float(scores[i]), **r))
            if len(out) >= top_k:
                break
        return out


class DbRetriever:
    """sources 앱 테이블을 검색 (Django DB 연결 사용).

    - Postgres + pgvector: SQL에서 코사인 거리로 바로 정렬
    - 그 밖(SQLite, pgvector 없는 Postgres): 후보 청크를 읽어서 numpy로 계산 (청크 수천 개까지는 충분히 빠름)

    테이블 (sources 앱 모델, P1-6):
      sources_sourcedocument: id, source_url, title, agency, region, published_at, collected_at, status('ACTIVE'만 검색)
      sources_sourcechunk   : id, source_document_id, chunk_text, chunk_order, embedding(JSON 숫자 배열, null이면 제외)
    """

    COLS = "c.id, c.chunk_text, {score}, d.source_url, d.title, d.agency, d.region, d.published_at, d.collected_at"

    def __init__(self):
        s = get_settings()
        for t in (s.source_document_table, s.source_chunk_table):
            if not t.replace("_", "").isalnum():
                raise ValueError(f"테이블 이름이 이상해: {t}")
        self.doc_t, self.chunk_t = s.source_document_table, s.source_chunk_table

    @staticmethod
    def _connection():
        from django.db import connection  # Django 안에서 쓰는 걸 전제로 함

        return connection

    def _uses_vector(self, conn) -> bool:
        if conn.vendor != "postgresql":
            return False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = to_regclass(%s) AND attname = 'embedding'",
                [self.chunk_t],
            )
            row = cur.fetchone()
        return bool(row and str(row[0]).startswith("vector"))

    def _where(self, regions):
        # 임베딩이 아직 없는 청크(null)는 검색 대상에서 뺀다
        sql = (
            f"FROM {self.chunk_t} c JOIN {self.doc_t} d ON d.id = c.source_document_id "
            "WHERE d.status = 'ACTIVE' AND c.embedding IS NOT NULL"
        )
        params: list = []
        if regions:
            sql += " AND d.region IN (" + ", ".join(["%s"] * len(regions)) + ")"
            params += list(regions)
        return sql, params

    @staticmethod
    def _row(r, score) -> SearchResult:
        return SearchResult(r[0], r[1], float(score), r[3], r[4], r[5], r[6], _as_date(r[7]), _as_date(r[8]) or date.today())

    def search(self, query: str, *, regions: list[str] | None, top_k: int, min_score: float) -> list[SearchResult]:
        q = np.asarray(get_embedder().embed_query(query), dtype=np.float32)
        conn = self._connection()
        where, params = self._where(regions)

        if self._uses_vector(conn):
            qv = to_pgvector(q)
            sql = (
                f"SELECT {self.COLS.format(score='1 - (c.embedding <=> %s::vector)')} {where} "
                "ORDER BY c.embedding <=> %s::vector LIMIT %s"
            )
            with conn.cursor() as cur:
                cur.execute(sql, [qv, *params, qv, top_k])
                rows = cur.fetchall()
            return [self._row(r, r[2]) for r in rows if r[2] is not None and float(r[2]) >= min_score]

        # numpy 계산 (embedding이 text로 저장된 경우)
        with conn.cursor() as cur:
            cur.execute(f"SELECT {self.COLS.format(score='c.embedding')} {where}", params)
            rows = cur.fetchall()
        if not rows:
            return []
        vecs, keep = [], []
        dim = len(q)
        for r in rows:  # JSONField는 DB에 따라 문자열(SQLite) 또는 리스트(Postgres jsonb)로 옴
            v = json.loads(r[2]) if isinstance(r[2], str) else r[2]
            if isinstance(v, list) and len(v) == dim:  # 차원이 다른(예전 모델) 임베딩은 건너뜀
                vecs.append(v)
                keep.append(r)
        if not keep:
            return []
        rows = keep
        m = np.asarray(vecs, dtype=np.float32)
        m /= np.where((n := np.linalg.norm(m, axis=1, keepdims=True)) == 0, 1, n)
        scores = m @ (q / (np.linalg.norm(q) or 1))
        order = [i for i in np.argsort(-scores)[:top_k] if scores[i] >= min_score]
        return [self._row(rows[i], scores[i]) for i in order]


# 예전 이름 호환
PgVectorRetriever = DbRetriever


@lru_cache(maxsize=1)
def get_retriever():
    kind = get_settings().retriever
    if kind == "memory":
        return MemoryRetriever()
    if kind in ("db", "pgvector"):
        return DbRetriever()
    raise RuntimeError(f"AI_RETRIEVER={kind}는 지원 안 해 (memory | db)")


def search(query: str, regions: list[str] | None = None, top_k: int | None = None, min_score: float | None = None):
    s = get_settings()
    return get_retriever().search(
        query,
        regions=regions,
        top_k=top_k or s.top_k,
        min_score=s.min_score if min_score is None else min_score,
    )
