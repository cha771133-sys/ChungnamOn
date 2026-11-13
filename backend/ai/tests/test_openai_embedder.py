"""OpenAI 임베딩: 요청 형식·묶음 전송·429 재시도·차원 검사·키 비노출. 실제 API는 호출하지 않는다 (httpx.MockTransport)."""
import json
import os
import unittest
from unittest import mock

import httpx
import numpy as np

from ai.chatbot.embedder import OpenAIEmbedder

DIM = 1536
KEY = "sk-test-SECRET-1234567890"


def vector(seed: int, dim: int = DIM) -> list[float]:
    return [float(seed + 1)] * dim


class Recorder:
    """요청을 기록하고, responses에 넣어 둔 (status, body, headers)를 순서대로 돌려줌. 비면 정상 응답."""

    def __init__(self, *responses, dim=DIM, reverse=False):
        self.requests: list[httpx.Request] = []
        self.responses = list(responses)
        self.dim, self.reverse = dim, reverse

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            status, body, headers = self.responses.pop(0)
            return httpx.Response(status, json=body, headers=headers or {})
        n = len(json.loads(request.content)["input"])
        data = [{"object": "embedding", "index": i, "embedding": vector(i, self.dim)} for i in range(n)]
        return httpx.Response(200, json={"object": "list", "data": data[::-1] if self.reverse else data})

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def make(recorder, sleeps=None, dim=DIM, model="text-embedding-3-large"):
    return OpenAIEmbedder(model, KEY, dim, 30.0, transport=httpx.MockTransport(recorder),
                          sleep=(sleeps.append if sleeps is not None else lambda s: None))


class OpenAIEmbedderTests(unittest.TestCase):
    def test_request_has_model_dimensions_and_bearer_header(self):
        rec = Recorder()
        emb = make(rec)
        emb.embed_query("주민세 납부 기간")
        req = rec.requests[0]
        self.assertEqual((req.method, str(req.url)), ("POST", "https://api.openai.com/v1/embeddings"))
        self.assertEqual(req.headers["authorization"], f"Bearer {KEY}")
        self.assertNotIn(KEY, str(req.url))  # 키는 URL에 안 넣음
        self.assertEqual(rec.bodies()[0], {"model": "text-embedding-3-large", "input": ["주민세 납부 기간"],
                                           "dimensions": 1536, "encoding_format": "float"})
        # 모델·차원 설정이 그대로 요청에 들어감
        rec2 = Recorder(dim=256)
        make(rec2, dim=256, model="text-embedding-3-small").embed_passages(["a"])
        self.assertEqual((rec2.bodies()[0]["model"], rec2.bodies()[0]["dimensions"]), ("text-embedding-3-small", 256))

    def test_batches_and_keeps_order_and_normalizes(self):
        rec = Recorder(reverse=True)  # 응답 순서가 섞여도 index로 맞춤
        out = make(rec).embed_passages([f"청크 {i}" for i in range(205)])
        self.assertEqual([len(b["input"]) for b in rec.bodies()], [100, 100, 5])
        self.assertEqual(len(out), 205)
        self.assertTrue(all(v.shape == (DIM,) for v in out))
        self.assertAlmostEqual(float(np.linalg.norm(out[0])), 1.0, places=5)
        self.assertEqual(make(Recorder()).embed_passages([]), [])

    def test_429_retry_uses_server_wait_then_succeeds(self):
        sleeps = []
        rec = Recorder((429, {"error": {"message": "rate limit"}}, {"retry-after-ms": "1500"}),
                       (503, {}, {"retry-after": "3"}),
                       (429, {}, None))
        out = make(rec, sleeps).embed_passages(["a", "b"])
        self.assertEqual(len(out), 2)
        self.assertEqual(sleeps, [1.5, 3.0, 4.0])  # ms 헤더 → 초 헤더 → 지수 백오프(2**2)
        self.assertEqual(len(rec.requests), 4)

    def test_retry_limits_query_short_passages_long(self):
        sleeps = []
        rec = Recorder(*[(429, {}, {"retry-after": "100"})] * 3)
        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            make(rec, sleeps).embed_query("질문")
        self.assertEqual((len(rec.requests), sleeps), (2, [2.0]))  # 질문: 1번만, 최대 2초

        sleeps = []
        rec = Recorder(*[(429, {}, {"retry-after": "100"})] * 10)
        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            make(rec, sleeps).embed_passages(["청크"])
        self.assertEqual((len(rec.requests), sleeps), (6, [60.0] * 5))  # 청크: 5번, 최대 60초

    def test_wrong_dimension_or_shape_is_error(self):
        with self.assertRaisesRegex(RuntimeError, "차원이 768인데 AI_EMBED_DIM=1536"):
            make(Recorder(dim=768)).embed_query("질문")
        bad = [(200, {"data": []}, None), (200, {"data": [{"embedding": vector(0)}]}, None), (200, [1, 2], None)]
        for response in bad:
            with self.subTest(response=response[1]), self.assertRaisesRegex(RuntimeError, "응답 형식"):
                make(Recorder(response)).embed_query("질문")

    def test_key_never_in_errors_logs_or_repr(self):
        body = {"error": {"message": f"Incorrect API key provided: {KEY}", "type": "invalid_request_error"}}
        for status in (401, 400):
            with self.assertLogs("ai.chatbot.embedder", level="WARNING") as logs:
                emb = make(Recorder((429, body, None), (status, body, None)))
                with self.assertRaises(RuntimeError) as ctx:
                    emb.embed_passages(["문서 원문"])
            text = str(ctx.exception) + repr(ctx.exception) + "\n".join(logs.output) + repr(emb)
            self.assertNotIn(KEY, text)
            self.assertNotIn("SECRET", text)
            self.assertNotIn("문서 원문", text)
            self.assertIn(f"HTTP {status}", str(ctx.exception))

        def boom(request):
            raise httpx.ConnectError(f"failed {KEY}", request=request)

        with self.assertRaises(RuntimeError) as ctx:
            OpenAIEmbedder("m", KEY, DIM, 5.0, transport=httpx.MockTransport(boom)).embed_query("q")
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_missing_key_is_error(self):
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY가 비어 있어"):
            OpenAIEmbedder("text-embedding-3-large", "", DIM, 5.0)

    def test_get_embedder_selects_openai_without_probe_call(self):
        from ai.chatbot.config import get_settings, reset_settings
        from ai.chatbot.embedder import get_embedder

        env = {"AI_EMBEDDER": "openai", "OPENAI_API_KEY": KEY, "AI_EMBED_DIM": "", "AI_OPENAI_EMBED_MODEL": ""}
        with mock.patch.dict(os.environ, env), mock.patch("httpx.Client.post") as post:
            reset_settings()
            try:
                emb = get_embedder()
                self.assertIsInstance(emb, OpenAIEmbedder)
                self.assertEqual((emb.model, emb.dim), ("text-embedding-3-large", 1536))  # 기본 모델·차원
                post.assert_not_called()  # 차원 확인용 호출(요금) 안 함
            finally:
                reset_settings()
        # 코드 기본 임베더는 그대로 (ollama)
        with mock.patch.dict(os.environ, {"AI_EMBEDDER": "", "AI_EMBED_DIM": ""}):
            reset_settings()
            try:
                self.assertEqual((get_settings().embedder, get_settings().embed_dim), ("ollama", 1024))
            finally:
                reset_settings()


if __name__ == "__main__":
    unittest.main()
