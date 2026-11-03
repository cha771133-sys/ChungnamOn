"""sources 앱(P1-6) 모델 위에서 DB 검색 확인. Django(manage.py test)로 돌릴 때만 실행된다."""
import os
import unittest
from unittest import mock

def _django_ready() -> bool:
    try:
        from django.conf import settings

        return settings.configured
    except Exception:  # Django 없이 python -m unittest로 돌릴 때
        return False


DJANGO = _django_ready()

if DJANGO:
    from django.test import TestCase
else:
    TestCase = unittest.TestCase

ENV = {"AI_EMBEDDER": "hash", "AI_EMBED_DIM": "1024", "AI_LLM_PROVIDER": "mock", "AI_RETRIEVER": "db", "AI_MIN_SCORE": "-1"}


@unittest.skipUnless(DJANGO, "Django 설정이 없어서 건너뜀 (manage.py test로 실행)")
class DbRetrieverOnSourcesModelTests(TestCase):
    def setUp(self):
        from django.utils import timezone

        from ai.chatbot import embed_passages
        from ai.chatbot.config import reset_settings
        from sources.models import SourceChunk, SourceDocument

        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_settings()
        self.addCleanup(reset_settings)

        def add(url, title, region, status, text, embed=True):
            d = SourceDocument.objects.create(
                source_url=url, title=title, agency="테스트청", region=region, category="세금",
                status=status, collected_at=timezone.now(),
            )
            SourceChunk.objects.create(
                source_document=d, chunk_order=0, chunk_text=text,
                embedding=embed_passages([text])[0] if embed else None,
            )

        add("https://a.test/asan", "아산 주민세", "아산시", "ACTIVE", "아산시 주민세 납부 기간은 8월 16일부터 31일")
        add("https://a.test/cheonan", "천안 주민세", "천안시", "ACTIVE", "천안시 주민세 납부 기간은 8월 16일부터 31일")
        add("https://a.test/pending", "확인 대기", "아산시", "PENDING", "아산시 주민세 납부 기간은 8월 16일부터 31일")
        add("https://a.test/noemb", "임베딩 없음", "아산시", "ACTIVE", "아산시 주민세 납부 기간", embed=False)
        add("https://a.test/nat", "전국 납부 방법", "전국", "ACTIVE", "지방세는 가상계좌와 위택스로 납부")

    def test_filters_status_region_and_null_embedding(self):
        from ai.chatbot.retriever import search

        titles = [r.title for r in search("아산시 주민세 납부 기간", regions=["아산시", "충남", "전국"], top_k=10)]
        self.assertEqual(titles[0], "아산 주민세")
        self.assertIn("전국 납부 방법", titles)
        for excluded in ("천안 주민세", "확인 대기", "임베딩 없음"):
            self.assertNotIn(excluded, titles)

    def test_generate_answer_uses_db(self):
        from ai.chatbot import generate_answer

        out = generate_answer({"title": "주민세", "agency": {"name": "아산시청 세무과"}}, "주민세 납부 기간", language="ko")
        self.assertEqual(out["grounding_status"], "GROUNDED")
        self.assertEqual(out["sources"][0]["url"], "https://a.test/asan")
