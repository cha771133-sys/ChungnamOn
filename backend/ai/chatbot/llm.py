"""LLM 교체 구조: anthropic(배포, Claude Haiku) / ollama(개발) / mock(키 없이 테스트).

모든 provider는 complete_json(system, messages) -> dict 하나만 구현함.
messages = [{"role": "user"|"assistant", "content": str}, ...]
"""
import json
import logging
import re
from functools import lru_cache

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


def extract_json(text: str) -> dict:
    """모델 출력에서 JSON 객체만 뽑기 (```json 블록이나 앞뒤 설명이 섞여도 처리)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSON을 찾지 못함: {text[:200]}")
    return json.loads(text[start : end + 1])


class AnthropicLLM:
    name = "anthropic"

    def __init__(self):
        import anthropic

        s = get_settings()
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.request_timeout)
        self.model = s.anthropic_model

    def complete_json(self, system: str, messages: list[dict], **_) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=system,
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return extract_json(text)


class OllamaLLM:
    name = "ollama"

    def __init__(self):
        s = get_settings()
        self.url = f"{s.ollama_url}/api/chat"
        self.model = s.ollama_model
        self.timeout = s.request_timeout
        self.num_ctx = s.ollama_num_ctx

    def complete_json(self, system: str, messages: list[dict], **_) -> dict:
        r = httpx.post(
            self.url,
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, *messages],
                "format": "json",
                "stream": False,
                # 기본 컨텍스트(4096)를 넘는 프롬프트는 앞부분이 조용히 잘려서 늘려 둠
                "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return extract_json(r.json()["message"]["content"])


class MockLLM:
    """API 키 없이 흐름만 확인하는 가짜 LLM. 답변 품질은 의미 없음."""
    name = "mock"

    def complete_json(self, system: str, messages: list[dict], *, mock_hint: dict | None = None, **_) -> dict:
        hint = mock_hint or {}
        if hint.get("task") == "rewrite":
            return {"query": hint.get("fallback_query", messages[-1]["content"])}
        source_ids = hint.get("source_ids", [])
        if hint.get("task") == "verify":
            return {"status": "MATCHED" if source_ids else "UNVERIFIED", "reason": "[MOCK]"}
        if source_ids:
            first = hint.get("first_source_text", "")[:160].replace("\n", " ")
            return {
                "answer": f"[MOCK 답변] 공식 자료 기준으로는: {first} …",
                "status": "GROUNDED",
                "used_sources": source_ids[:2],
                "suggested_questions": ["기한이 지나면 어떻게 되나요?", "어디서 낼 수 있나요?"],
            }
        return {
            "answer": "[MOCK 답변] 공식 자료에서 확인되지 않았어요. 담당기관에 문의해 주세요.",
            "status": "UNVERIFIED",
            "used_sources": [],
            "suggested_questions": ["담당기관 연락처가 어디예요?"],
        }


@lru_cache(maxsize=1)
def get_llm():
    s = get_settings()
    provider = s.llm_provider or ("anthropic" if s.anthropic_api_key else "mock")
    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("AI_LLM_PROVIDER=anthropic인데 ANTHROPIC_API_KEY가 비어 있어")
        return AnthropicLLM()
    if provider == "ollama":
        return OllamaLLM()
    log.warning("AI_LLM_PROVIDER=mock: 가짜 답변으로 동작함 (개발: ollama, 배포: anthropic)")
    return MockLLM()
