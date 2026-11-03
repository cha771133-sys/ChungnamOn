"""임베딩 (명세서 6장 ⑤, AI 담당).

- ollama    : 개발용. Ollama의 bge-m3 (다국어·한국어 검색에 강함, 1024차원)
- fastembed : 앱 안에서 직접 돌림 (Ollama 없는 서버용)
- hash      : 테스트용. 인터넷·모델 없이 돌아가지만 검색 품질은 낮음
"""
import hashlib
from functools import lru_cache

import numpy as np

from .config import get_settings


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
    if s.embedder == "fastembed":
        emb, name = FastEmbedEmbedder(s.fastembed_model), s.fastembed_model
    elif s.embedder == "ollama":
        emb, name = OllamaEmbedder(s.ollama_embed_model, s.ollama_url, s.request_timeout), s.ollama_embed_model
    else:
        raise RuntimeError(f"AI_EMBEDDER={s.embedder}는 지원 안 해 (ollama | fastembed | hash)")
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
