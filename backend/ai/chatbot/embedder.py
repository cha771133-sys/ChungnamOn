"""임베딩 (명세서 6장 ⑤, AI 담당).

- gemini    : Google gemini-embedding-001 API (GEMINI_API_KEY 필요, 기본 768차원)
- ollama    : 개발용. Ollama의 bge-m3 (다국어·한국어 검색에 강함, 1024차원)
- fastembed : 앱 안에서 직접 돌림 (Ollama 없는 서버용)
- hash      : 테스트용. 인터넷·모델 없이 돌아가지만 검색 품질은 낮음
"""
import hashlib
import logging
from functools import lru_cache

import numpy as np

from .config import get_settings

log = logging.getLogger(__name__)


class OllamaEmbedder:
    def __init__(self, model: str, url: str, timeout: float):
        import httpx

        self.model = model
        self.url = f"{url}/api/embed"
        self.http = httpx.Client(timeout=timeout)

    def _embed(self, texts: list[str]) -> list[np.ndarray]:
        r = self.http.post(self.url, json={"model": self.model, "input": texts})
        if r.status_code == 404:
            raise RuntimeError(f"Ollama에 {self.model} 모델이 없어. 먼저 'ollama pull {self.model}' 실행해.")
        r.raise_for_status()
        return [np.asarray(v, dtype=np.float32) for v in r.json()["embeddings"]]

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(texts), 32):
            out += self._embed(texts[i : i + 32])
        return out


class FastEmbedEmbedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding  # pip install fastembed (선택)

        self.model = TextEmbedding(model)
        self.e5 = "e5" in model.lower()  # e5 계열은 query/passage 접두어 필요

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(next(iter(self.model.embed([f"query: {text}" if self.e5 else text]))), dtype=np.float32)

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        ts = [f"passage: {t}" for t in texts] if self.e5 else texts
        return [np.asarray(v, dtype=np.float32) for v in self.model.embed(ts)]


class GeminiEmbedder:
    """Google Gemini 임베딩 API (REST v1beta batchEmbedContents).

    - 자료 저장: taskType=RETRIEVAL_DOCUMENT, 질문 검색: RETRIEVAL_QUERY
    - outputDimensionality=AI_EMBED_DIM으로 요청. 3072가 아닌 차원은 정규화되지 않은 채 오므로 직접 L2 정규화
    - 여러 문장은 한 요청에 묶음 (요청당 최대 100개)
    - 429(무료 등급 한도)·5xx는 잠깐 기다렸다가 재시도. 서버가 알려준 대기 시간(Retry-After, retryDelay)을 우선 사용
    - 연결·읽기 timeout 설정. API 키는 URL이 아니라 헤더(x-goog-api-key)로 보내서 오류 메시지에 안 남게 함
    """

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
    BATCH_SIZE = 100
    RETRY_STATUSES = (429, 500, 502, 503, 504)
    MAX_RETRIES = 5
    MAX_WAIT = 60.0

    def __init__(self, model: str, api_key: str, dim: int, timeout: float, *, transport=None, sleep=None):
        import time

        import httpx

        if not api_key:
            raise RuntimeError("AI_EMBEDDER=gemini인데 GEMINI_API_KEY가 비어 있어")
        self.model = model.removeprefix("models/")
        self.dim = dim
        self.url = self.URL.format(model=self.model)
        self.sleep = sleep or time.sleep
        self.http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            transport=transport,
        )

    @classmethod
    def _wait_seconds(cls, response, attempt: int) -> float:
        """서버가 준 대기 시간 → 없으면 지수 백오프(1, 2, 4, 8 ...초). 최대 MAX_WAIT."""
        wait = None
        header = response.headers.get("retry-after")
        if header:
            try:
                wait = float(header)
            except ValueError:
                wait = None
        if wait is None:
            try:
                for d in response.json().get("error", {}).get("details", []):
                    delay = str(d.get("retryDelay") or "")
                    if delay.endswith("s"):
                        wait = float(delay[:-1])
                        break
            except Exception:
                wait = None
        if wait is None:
            wait = float(2 ** attempt)
        return min(max(wait, 0.0), cls.MAX_WAIT)

    def _post(self, body: dict) -> dict:
        for attempt in range(self.MAX_RETRIES + 1):
            r = self.http.post(self.url, json=body)
            if r.status_code in self.RETRY_STATUSES and attempt < self.MAX_RETRIES:
                wait = self._wait_seconds(r, attempt)
                log.warning("Gemini 임베딩 HTTP %s, %.1f초 뒤 재시도 (%d/%d)", r.status_code, wait, attempt + 1, self.MAX_RETRIES)
                self.sleep(wait)
                continue
            if r.status_code >= 400:
                # 응답 본문에는 요청 문장이 섞일 수 있어서 상태 코드만 남김
                raise RuntimeError(f"Gemini 임베딩 요청 실패: HTTP {r.status_code}")
            return r.json()
        raise RuntimeError("Gemini 임베딩 재시도 횟수 초과")  # pragma: no cover (루프 안에서 끝남)

    def _embed(self, texts: list[str], task_type: str) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            data = self._post({
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": task_type,
                        "outputDimensionality": self.dim,
                    }
                    for t in batch
                ]
            })
            embeddings = data.get("embeddings") if isinstance(data, dict) else None
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise RuntimeError("Gemini 임베딩 응답 형식이 예상과 다름 (embeddings 개수 불일치)")
            for e in embeddings:
                v = np.asarray((e or {}).get("values") or [], dtype=np.float32)
                if v.shape != (self.dim,):
                    raise RuntimeError(f"Gemini 임베딩 차원이 {v.size}인데 AI_EMBED_DIM={self.dim}이야")
                norm = np.linalg.norm(v)
                out.append(v / norm if norm else v)
        return out

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "RETRIEVAL_QUERY")[0]

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT") if texts else []


class HashEmbedder:
    def __init__(self, dim: int):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        s = "".join(text.split())
        for n in (2, 3):
            for i in range(len(s) - n + 1):
                v[int.from_bytes(hashlib.md5(s[i : i + n].encode()).digest()[:4], "little") % self.dim] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return [self._vec(t) for t in texts]


@lru_cache(maxsize=1)
def get_embedder():
    s = get_settings()
    if s.embedder == "hash":
        return HashEmbedder(s.embed_dim)
    if s.embedder == "gemini":
        # 차원은 요청 파라미터로 정하고 응답마다 검사하므로, 확인용 호출(무료 한도 소모)은 하지 않음
        return GeminiEmbedder(s.gemini_embed_model, s.gemini_api_key, s.embed_dim, s.request_timeout)
    if s.embedder == "fastembed":
        emb, name = FastEmbedEmbedder(s.fastembed_model), s.fastembed_model
    elif s.embedder == "ollama":
        emb, name = OllamaEmbedder(s.ollama_embed_model, s.ollama_url, s.request_timeout), s.ollama_embed_model
    else:
        raise RuntimeError(f"AI_EMBEDDER={s.embedder}는 지원 안 해 (gemini | ollama | fastembed | hash)")
    dim = len(emb.embed_query("차원 확인"))
    if dim != s.embed_dim:
        raise RuntimeError(f"{name}의 차원은 {dim}인데 AI_EMBED_DIM={s.embed_dim}이야. 맞춰서 고쳐.")
    return emb


def embed_passages(texts: list[str]) -> list[list[float]]:
    """수집기가 호출하는 함수. source_chunk.embedding에 그대로 넣을 수 있는 float 리스트를 돌려줌."""
    return [v.tolist() for v in get_embedder().embed_passages(texts)]


def to_pgvector(vec) -> str:
    """pgvector 리터럴 문자열. raw SQL에서 %s::vector 로 넘길 때 사용."""
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"
