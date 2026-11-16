"""LLM 교체 구조: anthropic(배포, Claude Haiku) / ollama(개발) / mock(테스트 전용, AI_LLM_PROVIDER=mock 명시).

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
        # 모델 출력에는 질문·문서·답변이 섞여 있어서 로그로 새지 않게 길이만 남김
        raise ValueError(f"JSON을 찾지 못함 (출력 {len(text)}자)")
    return json.loads(text[start : end + 1])


class AnthropicLLM:
    name = "anthropic"

    def __init__(self):
        import anthropic

        s = get_settings()
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.request_timeout)
        self.model = s.anthropic_model
        self.max_tokens = s.max_tokens

    def complete_json(self, system: str, messages: list[dict], **_) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        if getattr(resp, "stop_reason", None) == "max_tokens":
            # 잘린 JSON을 억지로 파싱하지 않음. 베트남어·태국어 등은 같은 내용도 토큰이 많이 듦
            raise ValueError(f"응답이 max_tokens({self.max_tokens})에서 잘림. AI_MAX_TOKENS를 늘려야 함")
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
            # 공식 자료가 있으면 모든 항목을 첫 자료 근거로 MATCHED
            return {"items": [
                {"key": key, "status": "MATCHED", "officialValue": "[MOCK] 공식 값", "reason": "[MOCK]",
                 "sourceIds": source_ids[:1]} if source_ids else
                {"key": key, "status": "UNVERIFIED", "officialValue": None, "reason": "[MOCK]", "sourceIds": []}
                for key in hint.get("item_keys", [])
            ]}
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
    """AI_LLM_PROVIDER로 고른 LLM. 설정이 없거나 잘못되면 오류.

    mock은 AI_LLM_PROVIDER=mock으로 명시했을 때만 쓴다. 설정이 빠졌다고 mock으로 넘어가면
    가짜 MATCHED·GROUNDED가 사용자에게 나가므로, 오류를 내서 pipeline.verify는 UNVERIFIED,
    챗봇은 503이 되게 한다.
    """
    s = get_settings()
    provider = s.llm_provider
    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("AI_LLM_PROVIDER=anthropic인데 ANTHROPIC_API_KEY가 비어 있어")
        return AnthropicLLM()
    if provider == "ollama":
        return OllamaLLM()
    if provider == "mock":
        log.warning("AI_LLM_PROVIDER=mock: 가짜 답변으로 동작함 (테스트 전용)")
        return MockLLM()
    raise RuntimeError(f"AI_LLM_PROVIDER={provider or '(비어 있음)'}: anthropic | ollama | mock 중 하나로 지정해야 해")
