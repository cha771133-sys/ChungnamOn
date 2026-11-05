"""Gemini 임베딩: 요청 형식·묶음 전송·429 재시도·정규화. 실제 API는 호출하지 않는다 (httpx.MockTransport)."""
import json
import os
import unittest
from unittest import mock

import httpx
import numpy as np

from ai.chatbot.embedder import GeminiEmbedder

DIM = 768


def vector(seed: int) -> list[float]:
    return [float(seed + 1)] * DIM  # 정규화 안 된 값 (3072가 아닌 차원은 API가 정규화해 주지 않음)


class Recorder:
    """요청을 기록하고, responses에 넣어 둔 (status, body, headers)를 순서대로 돌려줌. 비면 정상 응답."""

    def __init__(self, *responses):
        self.requests: list[httpx.Request] = []
        self.responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            status, body, headers = self.responses.pop(0)
            return httpx.Response(status, json=body, headers=headers or {})
        n = len(json.loads(request.content)["requests"])
        return httpx.Response(200, json={"embeddings": [{"values": vector(i)} for i in range(n)]})

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def make(recorder, sleeps=None, **kw):
    return GeminiEmbedder(
        "gemini-embedding-001", "test-key", kw.pop("dim", DIM), 30.0,
        transport=httpx.MockTransport(recorder), sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


class GeminiEmbedderTests(unittest.TestCase):
    def test_request_format_and_task_types(self):
        rec = Recorder()
        emb = make(rec)
        emb.embed_passages(["문서 A", "문서 B"])
        emb.embed_query("질문")

        req = rec.requests[0]
        self.assertEqual(
            str(req.url),
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents",
        )
        self.assertEqual(req.headers["x-goog-api-key"], "test-key")
        self.assertNotIn("key=", str(req.url))  # 키는 URL에 안 넣음
        doc_body, query_body = rec.bodies()
        self.assertEqual(doc_body["requests"][0], {
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": "문서 A"}]},
            "taskType": "RETRIEVAL_DOCUMENT",
            "outputDimensionality": DIM,
        })
        self.assertEqual(len(doc_body["requests"]), 2)  # 여러 문장은 한 요청에
        self.assertEqual(query_body["requests"][0]["taskType"], "RETRIEVAL_QUERY")

    def test_timeouts_are_set(self):
        t = make(Recorder()).http.timeout
        self.assertEqual((t.connect, t.read), (10.0, 30.0))

    def test_batches_of_100(self):
        rec = Recorder()
        out = make(rec).embed_passages([f"t{i}" for i in range(205)])
        self.assertEqual([len(b["requests"]) for b in rec.bodies()], [100, 100, 5])
        self.assertEqual(len(out), 205)

    def test_vectors_are_l2_normalized(self):
        v = make(Recorder()).embed_query("q")
        self.assertEqual(v.shape, (DIM,))
        self.assertAlmostEqual(float(np.linalg.norm(v)), 1.0, places=5)

    def test_429_waits_server_delay_then_retries(self):
        rate = {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                    "retryDelay": "7s"}]}}
        rec = Recorder((429, rate, None), (429, {}, {"Retry-After": "3"}), (429, {}, None))
        sleeps = []
        out = make(rec, sleeps).embed_passages(["a"])
        self.assertEqual(sleeps, [7.0, 3.0, 4.0])  # retryDelay → Retry-After → 지수 백오프(2**2)
        self.assertEqual(len(rec.requests), 4)
        self.assertEqual(len(out), 1)

    def test_gives_up_after_max_retries_without_leaking_body(self):
        rec = Recorder(*[(429, {"error": {"message": "민감한 문장"}}, {"Retry-After": "999"})] * 10)
        sleeps = []
        with self.assertRaises(RuntimeError) as ctx:
            make(rec, sleeps).embed_passages(["a"])
        self.assertEqual(len(rec.requests), GeminiEmbedder.MAX_RETRIES + 1)
        self.assertEqual(sleeps, [GeminiEmbedder.MAX_WAIT] * GeminiEmbedder.MAX_RETRIES)  # 대기 상한
        self.assertIn("429", str(ctx.exception))
        self.assertNotIn("민감한", str(ctx.exception))

    def test_query_retries_once_and_waits_at_most_2s(self):
        """챗봇 요청 안에서 도는 질문 임베딩은 워커를 오래 붙잡지 않음."""
        rec = Recorder(*[(429, {}, {"Retry-After": "999"})] * 10)
        sleeps = []
        with self.assertRaises(RuntimeError):
            make(rec, sleeps).embed_query("q")
        self.assertEqual(len(rec.requests), 2)  # 처음 1번 + 재시도 1번
        self.assertEqual(sleeps, [2.0])

        rec = Recorder((503, {}, None))
        sleeps = []
        self.assertEqual(make(rec, sleeps).embed_query("q").shape, (DIM,))  # 1번 재시도로 성공
        self.assertEqual(sleeps, [1.0])  # 서버 지정 없으면 백오프 1초 (상한 2초 이내)

    def test_client_error_is_not_retried(self):
        rec = Recorder((400, {"error": {"message": "bad"}}, None))
        with self.assertRaises(RuntimeError):
            make(rec).embed_query("q")
        self.assertEqual(len(rec.requests), 1)

    def test_wrong_shape_or_dimension_fails(self):
        with self.assertRaises(RuntimeError):
            make(Recorder((200, {"embedding": {"values": vector(0)}}, None))).embed_query("q")
        with self.assertRaises(RuntimeError):
            make(Recorder((200, {"embeddings": [{"values": [1.0] * 10}]}, None))).embed_query("q")

    def test_missing_key(self):
        with self.assertRaises(RuntimeError):
            GeminiEmbedder("gemini-embedding-001", "", DIM, 30.0)

    def test_settings_select_gemini_with_768_default(self):
        from ai.chatbot.config import get_settings, reset_settings
        from ai.chatbot.embedder import get_embedder

        env = {"AI_EMBEDDER": "gemini", "GEMINI_API_KEY": "k", "AI_EMBED_DIM": "", "AI_GEMINI_EMBED_MODEL": ""}
        with mock.patch.dict(os.environ, env):
            reset_settings()
            try:
                self.assertEqual(get_settings().embed_dim, 768)
                emb = get_embedder()  # 확인용 API 호출 없이 만들어져야 함
                self.assertIsInstance(emb, GeminiEmbedder)
                self.assertEqual((emb.model, emb.dim), ("gemini-embedding-001", 768))
            finally:
                reset_settings()


class AnthropicLLMTests(unittest.TestCase):
    """실제 API 없이 응답 객체만 흉내 냄."""

    def make(self, text, stop_reason="end_turn"):
        from types import SimpleNamespace

        from ai.chatbot.llm import AnthropicLLM

        llm = AnthropicLLM.__new__(AnthropicLLM)
        llm.model, llm.max_tokens = "claude-haiku-5-5", 4096
        resp = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
        llm.client = SimpleNamespace(messages=SimpleNamespace(create=mock.Mock(return_value=resp)))
        return llm

    def test_truncated_response_is_clear_error(self):
        llm = self.make('{"answer": "긴 답변', stop_reason="max_tokens")
        with self.assertRaises(ValueError) as ctx:
            llm.complete_json("s", [{"role": "user", "content": "q"}])
        self.assertIn("max_tokens", str(ctx.exception))
        self.assertEqual(llm.client.messages.create.call_args.kwargs["max_tokens"], 4096)

    def test_parse_error_does_not_leak_model_output(self):
        llm = self.make('{"answer": "개인 문서 내용')
        with self.assertRaises(ValueError) as ctx:
            llm.complete_json("s", [{"role": "user", "content": "q"}])
        self.assertNotIn("개인 문서", str(ctx.exception))

    def test_ok(self):
        self.assertEqual(self.make('```json\n{"ok": true}\n```').complete_json("s", []), {"ok": True})


class LLMProviderTests(unittest.TestCase):
    """설정이 빠졌을 때 가짜 LLM으로 조용히 넘어가지 않는지."""

    def provider(self, **env):
        from ai.chatbot.config import reset_settings
        from ai.chatbot.llm import get_llm

        base = {"AI_LLM_PROVIDER": "", "ANTHROPIC_API_KEY": ""}
        with mock.patch.dict(os.environ, {**base, **env}):
            reset_settings()
            try:
                return get_llm()
            finally:
                reset_settings()

    def test_missing_provider_is_error_even_without_key(self):
        with self.assertRaises(RuntimeError):
            self.provider()

    def test_missing_provider_is_error_even_with_key(self):
        with self.assertRaises(RuntimeError):
            self.provider(ANTHROPIC_API_KEY="k")

    def test_unknown_provider_is_error(self):
        with self.assertRaises(RuntimeError):
            self.provider(AI_LLM_PROVIDER="gpt")

    def test_anthropic_without_key_is_error(self):
        with self.assertRaises(RuntimeError):
            self.provider(AI_LLM_PROVIDER="anthropic")

    def test_mock_only_when_explicit(self):
        from ai.chatbot.llm import MockLLM

        self.assertIsInstance(self.provider(AI_LLM_PROVIDER="mock"), MockLLM)
