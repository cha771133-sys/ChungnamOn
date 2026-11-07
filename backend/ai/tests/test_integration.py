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
               text=ASAN_TEXT, title=None, index=True, review="APPROVED", reprocessing=False):
        """AI가 쓸 수 있는(승인·재처리 완료) 공식자료를 기본으로 만든다. index=True면 청크·임베딩까지."""
        import hashlib

        from django.utils import timezone

        from ai.chatbot import build_source_chunks
        from sources.models import SourceDocument

        doc = SourceDocument.objects.create(
            source_url=f"https://{host}/{path}", title=title or f"자료 {path}", agency="아산시청", region=region,
            category="지방세", status=status, content_text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest() if text else "",
            review_status=review, ai_reprocessing_required=reprocessing,
            collected_at=timezone.now() if collected else None,
        )
        if index:
            build_source_chunks([doc.pk])
        return doc

    def set_body(self, doc, text):
        """관리자 승인으로 본문이 바뀐 것처럼 본문·해시를 바꾼다."""
        import hashlib

        doc.content_text, doc.content_hash = text, hashlib.sha256(text.encode()).hexdigest()
        doc.save(update_fields=["content_text", "content_hash"])


STATS0 = {"documents": 0, "chunks": 0, "skipped": 0, "completed": 0, "changed": 0}


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class BuildSourceChunksTests(AIEnvMixin, TestCase):
    def test_targets_only_approved_documents_with_body(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        doc = self.source("a", index=False)
        others = [self.source("b", status="INACTIVE", index=False),
                  self.source("r", review="PENDING_REVIEW", index=False),
                  self.source("j", review="REJECTED", index=False),
                  self.source("c", text="", index=False)]

        self.assertEqual(build_source_chunks(), {**STATS0, "documents": 1, "chunks": 1})
        chunk = SourceChunk.objects.get(source_document=doc)
        self.assertEqual(len(chunk.embedding), 1024)
        self.assertEqual(chunk.source_content_hash, doc.content_hash)  # 읽은 본문 해시 기록
        self.assertTrue(chunk.is_current)
        self.assertIn("[자료 a]", chunk.chunk_text)
        self.assertFalse(SourceChunk.objects.filter(source_document__in=others).exists())

    def test_unchanged_skipped_changed_appended_without_deleting_old(self):
        from ai.chatbot import build_source_chunks
        from ai.chatbot.retriever import search
        from sources.models import SourceChunk

        doc = self.source("a")
        old = SourceChunk.objects.get(source_document=doc)
        with mock.patch("ai.chatbot.indexing.embed_passages") as embed:
            self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1})
        embed.assert_not_called()  # 승인 본문이 그대로면 임베딩 API도 안 부름

        self.set_body(doc, "# 새 본문\n" + "바뀐 납부 안내 " * 200)
        stats = build_source_chunks()
        self.assertEqual(stats["documents"], 1)
        self.assertGreater(stats["chunks"], 1)
        # 이전 청크는 그대로 남고 ID·순서도 재사용하지 않음
        old.refresh_from_db()
        self.assertEqual((old.chunk_order, old.source_content_hash != doc.content_hash), (0, True))
        new = SourceChunk.objects.filter(source_document=doc, source_content_hash=doc.content_hash)
        self.assertEqual(new.count(), stats["chunks"])
        self.assertEqual(min(new.values_list("chunk_order", flat=True)), 1)
        self.assertTrue(all("바뀐 납부 안내" in t for t in new.values_list("chunk_text", flat=True)))
        # 검색은 현재 본문 청크만
        texts = [r.text for r in search("납부 안내", regions=["아산시"], top_k=50, min_score=-1)]
        self.assertTrue(texts and all("바뀐 납부 안내" in t for t in texts))

    def test_wrong_dimension_gets_new_chunks(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        from ai.chatbot.retriever import search

        doc = self.source("a")
        SourceChunk.objects.filter(source_document=doc).update(embedding=[0.1] * 768)
        self.assertEqual(build_source_chunks()["documents"], 1)
        dims = sorted(len(e) for e in SourceChunk.objects.filter(source_document=doc).values_list("embedding", flat=True))
        self.assertEqual(dims, [768, 1024])  # 이전 임베딩은 보존, 검색은 차원이 맞는 것만

        # 다시 실행해도 옛 차원 청크 때문에 또 만들지 않음 (CodeRabbit: 반복 실행 시 중복 생성)
        for _ in range(2):
            with mock.patch("ai.chatbot.indexing.embed_passages") as embed:
                self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1})
            embed.assert_not_called()
        self.assertEqual(SourceChunk.objects.filter(source_document=doc).count(), 2)
        self.assertEqual(len(search("주민세", regions=["아산시"], top_k=10, min_score=-1)), 1)  # 중복 없이 1개

    def test_reprocessing_completed_with_hash_check_but_not_activated(self):
        from ai.chatbot import build_source_chunks
        from ai.chatbot.retriever import search

        doc = self.source("a", status="PENDING", reprocessing=True, index=False)
        version = doc.version
        self.assertEqual(build_source_chunks(), {**STATS0, "documents": 1, "chunks": 1, "completed": 1})
        doc.refresh_from_db()
        self.assertFalse(doc.ai_reprocessing_required)
        self.assertEqual(doc.version, version + 1)  # 먼저 연 관리자 화면이 덮어쓰지 못하게
        self.assertEqual(doc.status, "PENDING")  # ACTIVE 전환은 관리자 몫
        self.assertEqual(search("주민세", regions=["아산시"], min_score=-1), [])
        doc.status = "ACTIVE"
        doc.save(update_fields=["status"])
        self.assertTrue(doc.ai_eligible)
        self.assertEqual(len(search("주민세", regions=["아산시"], min_score=-1)), 1)

    def test_already_chunked_reprocessing_is_just_completed(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceDocument

        doc = self.source("a")
        SourceDocument.objects.filter(pk=doc.pk).update(ai_reprocessing_required=True, status="PENDING")
        with mock.patch("ai.chatbot.indexing.embed_passages") as embed:
            self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1, "completed": 1})
        embed.assert_not_called()
        doc.refresh_from_db()
        self.assertFalse(doc.ai_reprocessing_required)

    def test_body_changed_during_embedding_is_not_saved(self):
        import hashlib

        from ai.chatbot import build_source_chunks
        from ai.chatbot.embedder import embed_passages as real_embed
        from sources.models import SourceChunk, SourceDocument

        doc = self.source("a", status="PENDING", reprocessing=True, index=False)

        def approve_new_body_meanwhile(chunks):
            SourceDocument.objects.filter(pk=doc.pk).update(content_hash=hashlib.sha256(b"new").hexdigest())
            return real_embed(chunks)

        with mock.patch("ai.chatbot.indexing.embed_passages", side_effect=approve_new_body_meanwhile):
            self.assertEqual(build_source_chunks(), {**STATS0, "changed": 1})
        self.assertFalse(SourceChunk.objects.filter(source_document=doc).exists())
        doc.refresh_from_db()
        self.assertTrue(doc.ai_reprocessing_required)  # 완료로 표시하지 않음


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class VerifyInterpretationTests(AIEnvMixin, TestCase):
    def test_returns_existing_format_with_source_document_id(self):
        from ai.chatbot import verify_interpretation

        good = self.source("good")
        province = self.source("province", region="충청남도", host="www.chungnam.go.kr")
        excluded = [
            self.source("pending", status="PENDING"),
            self.source("review", review="PENDING_REVIEW"),
            self.source("reproc", status="PENDING", reprocessing=True, index=False),
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

    def _prompt(self, official, raw=None):
        """chat_answer가 LLM에 보낸 마지막 user 메시지와 결과."""
        from ai.chatbot import chat_answer

        raw = raw or {"answer": "답", "status": "UNVERIFIED", "used_sources": [], "suggested_questions": []}
        with mock.patch("ai.chatbot.llm.MockLLM.complete_json", return_value=raw) as llm:
            out = chat_answer("주민세 납부 기간이 언제예요?", self.interpretation(), [], "ko", official)
        return llm.call_args.args[1][-1]["content"], llm.call_args.kwargs["mock_hint"], out

    def test_unlinked_sources_go_to_reference_section_only(self):
        linked = self.source("linked", title="연결된 자료")
        self.source("other", title="연결 안 된 자료")
        prompt, hint, _ = self._prompt(self.official(linked))
        official_part, reference_part = prompt.split("[참고 자료 — 공식 출처로 표시 불가]")
        self.assertIn("S1 | 연결된 자료", official_part)
        self.assertNotIn("연결 안 된 자료", official_part)
        self.assertIn("R1 | 연결 안 된 자료", reference_part)
        self.assertNotIn("S2", prompt)
        self.assertEqual(hint["source_ids"], ["S1"])

    def test_no_official_sources_means_empty_official_section(self):
        self.source("other", title="연결 안 된 자료")
        prompt, hint, _ = self._prompt([])
        self.assertIn("[공식 자료]\n(검색된 공식 자료 없음)", prompt)
        self.assertIn("R1 | 연결 안 된 자료", prompt)
        self.assertEqual(hint["source_ids"], [])

    def test_official_source_not_pushed_out_by_unlinked_results(self):
        """연결 안 된 자료가 상위 top_k를 다 차지해도 연결된 공식 자료가 [공식 자료]에 들어감 (CodeRabbit)."""
        from ai.chatbot.config import get_settings

        k = get_settings().top_k
        # 질문과 똑같은 본문이라 점수가 더 높은 '연결 안 된' 자료 k개 + 덜 비슷한 '연결된' 자료 1개
        question = "주민세 납부 기간이 언제예요?"
        for i in range(k):
            self.source(f"other{i}", title=f"연결 안 된 자료 {i}", text=question)
        linked = self.source("linked", title="연결된 자료", text="# 주민세\n아산시 주민세 납부 안내입니다. " + "세무과 문의. " * 5)
        prompt, hint, _ = self._prompt(self.official(linked))
        official_part, reference_part = prompt.split("[참고 자료 — 공식 출처로 표시 불가]")
        self.assertIn("S1 | 연결된 자료", official_part)
        self.assertEqual(hint["source_ids"], ["S1"])
        self.assertLessEqual(reference_part.count("| 연결 안 된 자료"), k)  # 참고 자료도 k개까지만

    def test_reference_numbers_are_never_sources(self):
        linked = self.source("linked", title="연결된 자료")
        self.source("other", title="연결 안 된 자료")
        raw = {"answer": "답", "status": "GROUNDED", "used_sources": ["R1"], "suggested_questions": []}
        _, _, out = self._prompt(self.official(linked), raw)
        self.assertEqual((out["status"], out["sources"]), ("UNVERIFIED", []))
        raw["used_sources"] = ["R1", "S1"]
        _, _, out = self._prompt(self.official(linked), raw)
        self.assertEqual((out["status"], out["sources"]), ("GROUNDED", [{"sourceDocumentId": linked.pk}]))

    def test_rules_forbid_official_wording_without_official_sources(self):
        from ai.chatbot import prompts

        for phrase in ("공식 자료에 따르면", "UNVERIFIED 답변에서는 아예 쓰지 마", "R 번호는 절대 넣지 마",
                       "이 문서의 공식 출처로 확인된 내용은 아니에요"):
            self.assertIn(phrase, prompts.ANSWER_SYSTEM)

    def test_question_is_not_logged(self):
        self.source("a")
        with self.assertLogs("ai.chatbot.service", "INFO") as logs:
            self._prompt([])
        self.assertNotIn("주민세 납부 기간", "\n".join(logs.output))

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
