"""Django 교체 자리(ai_client.generate_answer, pipeline.verify)와 청크 생성 연결 확인.

해시 임베딩 + mock LLM이라 Ollama·키 없이 돈다. Django(manage.py test)로 돌릴 때만 실행된다.
"""
import os
import unittest
from unittest import mock


def _django_ready() -> bool:
    try:
        from django.conf import settings

        return settings.configured
    except Exception:
        return False


DJANGO = _django_ready()

if DJANGO:
    from django.test import TestCase, override_settings
else:  # pragma: no cover
    TestCase = unittest.TestCase

    def override_settings(**_):
        return lambda f: f

ENV = {"AI_EMBEDDER": "hash", "AI_EMBED_DIM": "1024", "AI_LLM_PROVIDER": "mock", "AI_RETRIEVER": "db", "AI_MIN_SCORE": "-1"}
ASAN_TEXT = "# 주민세 납부\n아산시 주민세(개인분) 납부 기간은 8월 16일부터 8월 31일까지입니다. 가상계좌와 위택스로 납부할 수 있습니다."
RESULT = {
    "doc_type": "고지서", "title": "주민세 납부 안내", "original_text": "주민세 6,250원 납부기한 8월 31일",
    "summary": ["2026년 8월 31일까지 주민세를 내야 합니다."], "terms": [],
    "deadlines": [{"label": "납부 마감", "date": "2026-08-31"}], "required_docs": ["고지서"],
    "agency": {"name": "아산시청 세무과", "phone": "041-000-0000", "source": "document"},
}


class AIEnvMixin:
    def setUp(self):
        from ai.chatbot.config import reset_settings

        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_settings()
        self.addCleanup(reset_settings)

    def source(self, path, *, region="아산시", status="ACTIVE", collected=True, host="www.asan.go.kr",
               text=ASAN_TEXT, title=None, index=True):
        from django.utils import timezone

        from ai.chatbot import build_source_chunks
        from sources.models import SourceDocument

        doc = SourceDocument.objects.create(
            source_url=f"https://{host}/{path}", title=title or f"자료 {path}", agency="아산시청", region=region,
            category="지방세", status=status, content_text=text,
            collected_at=timezone.now() if collected else None,
        )
        if index:
            build_source_chunks([doc.pk])
        return doc


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class BuildSourceChunksTests(AIEnvMixin, TestCase):
    def test_only_active_documents_with_body(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        doc = self.source("a", index=False)
        others = [self.source("b", status="INACTIVE", index=False), self.source("p", status="PENDING", index=False),
                  self.source("c", text="", index=False)]

        self.assertEqual(build_source_chunks(), {"documents": 1, "chunks": 1, "skipped": 0})
        chunk = SourceChunk.objects.get(source_document=doc)
        self.assertEqual(len(chunk.embedding), 1024)
        self.assertIn("[자료 a]", chunk.chunk_text)
        self.assertFalse(SourceChunk.objects.filter(source_document__in=others).exists())

    def test_unchanged_skipped_changed_rebuilt(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        doc = self.source("a")
        first_id = SourceChunk.objects.get(source_document=doc).pk
        with mock.patch("ai.chatbot.indexing.embed_passages") as embed:
            self.assertEqual(build_source_chunks(), {"documents": 0, "chunks": 0, "skipped": 1})
        embed.assert_not_called()  # 본문이 그대로면 임베딩 API도 안 부름
        self.assertEqual(SourceChunk.objects.get(source_document=doc).pk, first_id)

        doc.content_text = "# 새 본문\n" + "바뀐 납부 안내 " * 200
        doc.save(update_fields=["content_text"])
        stats = build_source_chunks()
        self.assertEqual(stats["documents"], 1)
        self.assertGreater(stats["chunks"], 1)
        texts = list(SourceChunk.objects.filter(source_document=doc).values_list("chunk_text", flat=True))
        self.assertEqual(len(texts), stats["chunks"])
        self.assertTrue(all("바뀐 납부 안내" in t for t in texts))

    def test_wrong_dimension_or_missing_embedding_is_rebuilt(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        doc = self.source("a")
        for bad in ([0.1] * 768, None):
            SourceChunk.objects.filter(source_document=doc).update(embedding=bad)
            self.assertEqual(build_source_chunks()["documents"], 1)
            self.assertEqual(len(SourceChunk.objects.get(source_document=doc).embedding), 1024)

    def test_rebuild_forces(self):
        from ai.chatbot import build_source_chunks

        self.source("a")
        self.assertEqual(build_source_chunks(rebuild=True)["documents"], 1)


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class VerifyInterpretationTests(AIEnvMixin, TestCase):
    def test_returns_existing_format_with_source_document_id(self):
        from ai.chatbot import verify_interpretation

        good = self.source("good")
        province = self.source("province", region="충청남도", host="www.chungnam.go.kr")
        excluded = [
            self.source("pending", status="PENDING"),
            self.source("nocheck", collected=False),
            self.source("cheonan", region="천안시"),
            self.source("unofficial", host="example.com"),
        ]
        out = verify_interpretation(RESULT)

        self.assertEqual(out["status"], "MATCHED")
        ids = [s["sourceDocumentId"] for s in out["sources"]]
        self.assertCountEqual(ids, [good.pk, province.pk])
        for d in excluded:
            self.assertNotIn(d.pk, ids)
        first = out["sources"][0]
        self.assertEqual(set(first), {"title", "url", "checkedAt", "sourceDocumentId"})
        self.assertRegex(first["checkedAt"], r"^\d{4}-\d{2}-\d{2}$")

    def test_at_most_ten_distinct_documents(self):
        from ai.chatbot import verify_interpretation
        from sources.models import SourceChunk

        for i in range(12):
            self.source(f"n{i}", text=ASAN_TEXT + ("\n\n# 추가\n" + "납부 안내 " * 120) * 3)
        self.assertGreater(SourceChunk.objects.count(), 12)
        ids = [s["sourceDocumentId"] for s in verify_interpretation(RESULT)["sources"]]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)

    def test_unidentified_agency_or_no_results_is_unverified(self):
        from ai.chatbot import verify_interpretation

        self.source("a")
        self.assertEqual(verify_interpretation({**RESULT, "agency": {"name": ""}}),
                         {"status": "UNVERIFIED", "sources": []})
        with mock.patch("ai.chatbot.integration.search", return_value=[]):
            self.assertEqual(verify_interpretation(RESULT), {"status": "UNVERIFIED", "sources": []})

    def test_invalid_llm_status_becomes_unverified_but_keeps_related(self):
        from ai.chatbot import verify_interpretation

        doc = self.source("a")
        with mock.patch("ai.chatbot.llm.MockLLM.complete_json", return_value={"status": "maybe"}):
            out = verify_interpretation(RESULT)
        self.assertEqual(out["status"], "UNVERIFIED")
        self.assertEqual(out["sources"][0]["sourceDocumentId"], doc.pk)

    @override_settings(DOCUMENTS_VERIFY_MODE="rag")
    def test_pipeline_slot_uses_rag(self):
        from documents.services import pipeline

        doc = self.source("a")
        out = pipeline.verify(RESULT)
        self.assertEqual(out["sources"][0]["sourceDocumentId"], doc.pk)

    @override_settings(DOCUMENTS_VERIFY_MODE="rag", DOCUMENTS_STEP_DELAY=0)
    def test_pipeline_errors_become_unverified_and_document_still_done(self):
        from django.contrib.auth import get_user_model

        from documents.models import Document, InterpretationResult
        from documents.services import pipeline

        self.source("a")
        for target in ("ai.chatbot.integration.search", "ai.chatbot.llm.MockLLM.complete_json"):
            with mock.patch(target, side_effect=RuntimeError("문서 내용 포함 오류")), \
                    self.assertLogs("documents.services.pipeline", "WARNING") as logs:
                self.assertEqual(pipeline.verify(RESULT), {"status": "UNVERIFIED", "sources": []})
            self.assertNotIn("문서 내용", "\n".join(logs.output))

        user = get_user_model().objects.create_user("verify-fail@example.com")
        document = Document.objects.create(owner=user, file="documents/t.pdf", file_name="t.pdf", file_type="PDF")
        with mock.patch("ai.chatbot.integration.search", side_effect=TimeoutError):
            self.assertTrue(pipeline.process_document(document.pk))
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.DONE)
        self.assertEqual(InterpretationResult.objects.get(document=document).verification,
                         {"status": "UNVERIFIED", "sources": []})

    def test_pipeline_slot_default_is_dummy(self):
        from documents.services import pipeline

        with mock.patch("ai.chatbot.integration.verify_interpretation") as rag:
            out = pipeline.verify(RESULT)
        rag.assert_not_called()
        self.assertNotIn("sourceDocumentId", out["sources"][0])


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class ChatAnswerTests(AIEnvMixin, TestCase):
    def interpretation(self, verification_status="MATCHED", ids=()):
        return {
            **{k: RESULT[k] for k in ("original_text", "summary", "terms", "deadlines", "required_docs", "agency")},
            "translated_text": "", "language": "ko",
            "verification": {"status": verification_status, "sources": [{"sourceDocumentId": i} for i in ids]},
        }

    def official(self, *docs):
        return [{"sourceDocumentId": d.pk} for d in docs]

    def ask(self, interpretation, official, **llm):
        from ai.chatbot import chat_answer

        if llm:
            with mock.patch("ai.chatbot.llm.MockLLM.complete_json", return_value=llm):
                return chat_answer("주민세 납부 기간이 언제예요?", interpretation, [], "ko", official)
        return chat_answer("주민세 납부 기간이 언제예요?", interpretation, [], "ko", official)

    def test_grounded_only_with_linked_official_source(self):
        doc = self.source("a")
        out = self.ask(self.interpretation(ids=[doc.pk]), self.official(doc))
        # 계약 키만 (agency_contact·grounding_status·snake_case 키는 버림)
        self.assertEqual(set(out), {"answer", "language", "sources", "grounded", "status", "suggestedQuestions"})
        self.assertEqual(out["status"], "GROUNDED")
        self.assertIs(out["grounded"], True)
        self.assertEqual(out["sources"], [{"sourceDocumentId": doc.pk}])
        self.assertEqual(out["language"], "ko")
        self.assertTrue(out["suggestedQuestions"])

    def test_unlinked_source_only_is_unverified(self):
        self.source("a")
        out = self.ask(self.interpretation(), [])
        self.assertEqual((out["status"], out["grounded"], out["sources"]), ("UNVERIFIED", False, []))

    def test_conflict_maps_to_mismatch_only_when_document_mismatch(self):
        doc = self.source("a")
        raw = {"answer": "다름", "status": "CONFLICT", "used_sources": ["S1"], "suggested_questions": []}
        out = self.ask(self.interpretation("MISMATCH", [doc.pk]), self.official(doc), **raw)
        self.assertEqual((out["status"], out["sources"]), ("MISMATCH", [{"sourceDocumentId": doc.pk}]))
        out = self.ask(self.interpretation("MATCHED", [doc.pk]), self.official(doc), **raw)
        self.assertEqual((out["status"], out["sources"]), ("UNVERIFIED", []))

    def test_suggestions_are_cleaned(self):
        raw = {"answer": "답", "status": "UNVERIFIED", "used_sources": [],
               "suggested_questions": [" a ", "a", "", "x" * 201, "b", "c", "d", "e", "f"]}
        with mock.patch("ai.chatbot.service.search", return_value=[]):
            out = self.ask(self.interpretation(), [], **raw)
        self.assertEqual(out["suggestedQuestions"], ["a", "b", "c", "d", "e"])

    def test_history_is_converted_to_turns(self):
        from ai.chatbot import chat_answer

        with mock.patch("ai.chatbot.integration.answer_with_results",
                        return_value=("답", None, [], [], "ko")) as run:
            chat_answer("q", self.interpretation(), [{"question": "이전 질문", "answer": "이전 답"}], "ko", [])
        self.assertEqual(run.call_args.kwargs["history"], [
            {"role": "user", "content": "이전 질문"}, {"role": "assistant", "content": "이전 답"},
        ])


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class AIClientSlotTests(AIEnvMixin, TestCase):
    ARGS = dict(question="q", interpretation={}, history=[], language="ko", official_sources=[])

    @override_settings(CHATBOT_AI_MODE="rag")
    def test_every_exception_becomes_ai_client_error(self):
        from chatbot.services import ai_client

        for exc in (RuntimeError("secret"), ValueError(), KeyError("k"), ImportError(), TimeoutError()):
            with mock.patch("ai.chatbot.integration.chat_answer", side_effect=exc):
                with self.assertRaises(ai_client.AIClientError) as ctx:
                    ai_client.generate_answer(**self.ARGS)
            self.assertIsNone(ctx.exception.__cause__)
            self.assertTrue(ctx.exception.__suppress_context__)  # 원문 예외를 연결하지 않음

    @override_settings(CHATBOT_AI_MODE="rag")
    def test_rag_mode_delegates(self):
        from chatbot.services import ai_client

        with mock.patch("ai.chatbot.integration.chat_answer", return_value={"ok": 1}) as run:
            self.assertEqual(ai_client.generate_answer(**self.ARGS), {"ok": 1})
        run.assert_called_once_with("q", {}, [], "ko", [])

    def test_default_dummy_does_not_touch_rag(self):
        from chatbot.services import ai_client

        with mock.patch("ai.chatbot.integration.chat_answer") as run:
            out = ai_client.generate_answer(**self.ARGS)
        run.assert_not_called()
        self.assertEqual(out["status"], "UNVERIFIED")


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class ChatApiRagEndToEndTests(AIEnvMixin, TestCase):
    """팀원 chatbot API를 그대로 통과하는지: 계약 검증·스냅샷 저장까지."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import AccessToken

        from ai.chatbot import verify_interpretation
        from documents.models import Document, InterpretationResult

        self.doc = self.source("a")
        user = get_user_model().objects.create_user("rag@example.com")
        self.document = Document.objects.create(
            owner=user, file="documents/t.pdf", file_name="t.pdf", file_type="PDF", status=Document.Status.DONE,
        )
        InterpretationResult.objects.create(
            document=self.document, translated_text="", language="ko",
            verification=verify_interpretation(RESULT),
            **{k: RESULT[k] for k in ("doc_type", "title", "original_text", "summary", "terms",
                                      "deadlines", "required_docs", "agency")},
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
        self.url = f"/api/documents/{self.document.pk}/chat/"

    @override_settings(CHATBOT_AI_MODE="rag")
    def test_post_saves_grounded_snapshot(self):
        res = self.client.post(self.url, {"message": "주민세 납부 기간이 언제예요?"}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["status"], "GROUNDED")
        self.assertEqual(body["sources"][0]["sourceDocumentId"], self.doc.pk)
        self.assertEqual(body["sources"][0]["url"], self.doc.source_url)

    @override_settings(CHATBOT_AI_MODE="rag")
    def test_ai_failure_is_503(self):
        with mock.patch("ai.chatbot.integration.answer_with_results", side_effect=RuntimeError("secret 질문")):
            res = self.client.post(self.url, {"message": "질문"}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("secret", res.content.decode())
