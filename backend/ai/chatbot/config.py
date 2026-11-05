"""챗봇 AI 설정. 전부 환경변수로 받는다 (Django settings.py가 backend/.env를 먼저 읽어 줌)."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]

# 충남 15개 시·군 (발신기관 이름에서 지역을 뽑을 때 사용)
CHUNGNAM_REGIONS = [
    "천안시", "공주시", "보령시", "아산시", "서산시", "논산시", "계룡시", "당진시",
    "금산군", "부여군", "서천군", "청양군", "홍성군", "예산군", "태안군",
]


@dataclass(frozen=True)
class Settings:
    # LLM: anthropic | ollama | mock  (비우면 ANTHROPIC_API_KEY 있으면 anthropic, 없으면 mock)
    llm_provider: str
    anthropic_api_key: str
    anthropic_model: str
    ollama_url: str
    ollama_model: str
    ollama_num_ctx: int
    # 임베딩: gemini | ollama | fastembed | hash(테스트용)
    embedder: str
    gemini_api_key: str
    gemini_embed_model: str
    ollama_embed_model: str
    fastembed_model: str
    embed_dim: int
    # 검색: db(sources 앱 테이블, 기본) | memory(AI_KB_DIR의 .md를 메모리에, Django 없이 개발)
    retriever: str
    kb_dir: Path
    source_document_table: str
    source_chunk_table: str
    top_k: int
    min_score: float
    chunk_size: int
    chunk_overlap: int
    min_chunk_chars: int
    history_turns: int
    request_timeout: float


def _env(name: str, default: str) -> str:
    # .env.example을 그대로 복사하면 `AI_EMBED_DIM=`처럼 빈 값이 들어오므로, 비어 있으면 기본값을 쓴다
    return os.environ.get(name, "").strip() or default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    embedder = _env("AI_EMBEDDER", "ollama")
    return Settings(
        llm_provider=_env("AI_LLM_PROVIDER", ""),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        anthropic_model=_env("AI_ANTHROPIC_MODEL", "claude-haiku-5-5"),
        ollama_url=_env("AI_OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=_env("AI_OLLAMA_MODEL", "qwen2.5:7b"),
        ollama_num_ctx=int(_env("AI_OLLAMA_NUM_CTX", "8192")),
        embedder=embedder,
        gemini_api_key=_env("GEMINI_API_KEY", ""),
        gemini_embed_model=_env("AI_GEMINI_EMBED_MODEL", "gemini-embedding-001"),
        ollama_embed_model=_env("AI_OLLAMA_EMBED_MODEL", "bge-m3"),
        fastembed_model=_env("AI_FASTEMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
        # gemini는 요청으로 차원을 고름 (권장 768/1536/3072). bge-m3는 1024 고정
        embed_dim=int(_env("AI_EMBED_DIM", "768" if embedder == "gemini" else "1024")),
        retriever=_env("AI_RETRIEVER", "db"),
        kb_dir=Path(_env("AI_KB_DIR", str(BACKEND / "sources" / "data" / "pages"))),
        source_document_table=_env("AI_SOURCE_DOCUMENT_TABLE", "sources_sourcedocument"),
        source_chunk_table=_env("AI_SOURCE_CHUNK_TABLE", "sources_sourcechunk"),
        top_k=int(_env("AI_TOP_K", "5")),
        min_score=float(_env("AI_MIN_SCORE", "0.35")),
        chunk_size=int(_env("AI_CHUNK_SIZE", "800")),
        chunk_overlap=int(_env("AI_CHUNK_OVERLAP", "100")),
        min_chunk_chars=int(_env("AI_MIN_CHUNK_CHARS", "150")),
        history_turns=int(_env("AI_HISTORY_TURNS", "6")),
        request_timeout=float(_env("AI_REQUEST_TIMEOUT", "120")),
    )


def reset_settings():
    """테스트에서 환경변수를 바꾼 뒤 호출. 캐시된 모델·검색기도 같이 비운다."""
    get_settings.cache_clear()
    from . import embedder, llm, retriever

    embedder.get_embedder.cache_clear()
    llm.get_llm.cache_clear()
    retriever.get_retriever.cache_clear()
