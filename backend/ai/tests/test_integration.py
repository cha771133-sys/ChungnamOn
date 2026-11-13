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
# C1 예시 모양: 우진님이 추가할 amount(문자열 또는 null)·payment_methods(문자열 목록)까지 있는 해석 결과
C1_RESULT = {**RESULT, "amount": "6,250원", "payment_methods": ["은행 창구", "가상계좌", "위택스"]}


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


STATS0 = {"documents": 0, "chunks": 0, "skipped": 0, "completed": 0, "activated": 0, "changed": 0}
# 자동 ACTIVE 전환(완료 처리)이 바꿔도 되는 칸. 나머지(제목·기관 등 관리자 입력)는 그대로여야 함
COMPLETION_FIELDS = {"status", "ai_reprocessing_required", "version", "updated_at"}


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

    # ── 자동 ACTIVE 전환 (REFRESH.md 'AI 경계' 정책, #60) ──
    def pending(self, path="a", **kw):
        """승인 후 재처리 대기(PENDING, ai_reprocessing_required=True)인 자료. 청크 없음."""
        return self.source(path, status="PENDING", reprocessing=True, index=False, **kw)

    def run_while(self, change):
        """임베딩하는 동안(처리 시작 뒤·완료 전) change()로 DB를 바꾸면서 build_source_chunks 실행."""
        from ai.chatbot import build_source_chunks
        from ai.chatbot.embedder import embed_passages as real_embed

        def embed_and_change(chunks):
            change()
            return real_embed(chunks)

        with mock.patch("ai.chatbot.indexing.embed_passages", side_effect=embed_and_change):
            return build_source_chunks()

    @staticmethod
    def row(doc):
        from django.forms.models import model_to_dict

        from sources.models import SourceDocument

        return model_to_dict(SourceDocument.objects.get(pk=doc.pk))

    def assert_only_completion_fields_changed(self, before, doc):
        after = self.row(doc)
        changed = {k for k in after if after[k] != before[k]}
        self.assertLessEqual(changed, COMPLETION_FIELDS)

    def test_normal_completion_activates(self):
        from ai.chatbot import build_source_chunks
        from ai.chatbot.retriever import search
        from sources.models import SourceChunk

        doc = self.pending()
        before = self.row(doc)
        self.assertEqual(search("주민세", regions=["아산시"], min_score=-1), [])
        self.assertEqual(build_source_chunks(),
                         {**STATS0, "documents": 1, "chunks": 1, "completed": 1, "activated": 1})
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.ai_reprocessing_required, doc.version),
                         ("ACTIVE", False, before["version"] + 1))
        self.assertTrue(doc.ai_eligible)
        self.assertTrue(SourceChunk.objects.get(source_document=doc).is_current)
        self.assertEqual(len(search("주민세", regions=["아산시"], min_score=-1)), 1)
        self.assert_only_completion_fields_changed(before, doc)

    def test_repeated_runs_keep_one_chunk_set_and_one_version_bump(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        doc = self.pending()
        version = doc.version
        build_source_chunks()
        for _ in range(2):
            with mock.patch("ai.chatbot.indexing.embed_passages") as embed:
                self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1})
            embed.assert_not_called()
        doc.refresh_from_db()
        self.assertEqual(SourceChunk.objects.filter(source_document=doc).count(), 1)
        self.assertEqual((doc.status, doc.version), ("ACTIVE", version + 1))

    def test_admin_edit_during_processing_is_not_overwritten(self):
        from django.db.models import F

        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk, SourceDocument

        doc = self.pending()
        version = doc.version

        def admin_edits():  # 본문은 그대로, 제목·기관만 고치고 저장 → version+1
            SourceDocument.objects.filter(pk=doc.pk).update(
                title="관리자가 고친 제목", agency="관리자가 고친 기관", version=F("version") + 1)

        self.assertEqual(self.run_while(admin_edits), {**STATS0, "changed": 1})
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.ai_reprocessing_required, doc.version), ("PENDING", True, version + 1))
        self.assertEqual((doc.title, doc.agency), ("관리자가 고친 제목", "관리자가 고친 기관"))
        self.assertFalse(SourceChunk.objects.filter(source_document=doc).exists())  # 이 작업의 청크는 안 남김

        # 다음 실행은 새 시작 값으로 정상 완료. 관리자 수정은 그대로
        self.assertEqual(build_source_chunks()["activated"], 1)
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.version, doc.title), ("ACTIVE", version + 2, "관리자가 고친 제목"))

    def test_inactive_or_unapproved_during_processing_is_not_activated(self):
        from ai.chatbot.retriever import search
        from sources.models import SourceChunk, SourceDocument

        # version은 그대로 두고 상태만 바꿔서, 상태 재확인만으로도 막히는지 본다
        for path, change, expected in (
            ("off", {"status": "INACTIVE"}, ("INACTIVE", "APPROVED")),
            ("revoked", {"review_status": "PENDING_REVIEW"}, ("PENDING", "PENDING_REVIEW")),
            ("rejected", {"review_status": "REJECTED"}, ("PENDING", "REJECTED")),
        ):
            with self.subTest(change=change):
                doc = self.pending(path)
                before = self.row(doc)
                stats = self.run_while(lambda: SourceDocument.objects.filter(pk=doc.pk).update(**change))
                self.assertEqual(stats, {**STATS0, "changed": 1})
                doc.refresh_from_db()
                self.assertEqual((doc.status, doc.review_status), expected)
                self.assertEqual((doc.ai_reprocessing_required, doc.version), (True, before["version"]))
                self.assertFalse(SourceChunk.objects.filter(source_document=doc).exists())
        self.assertEqual(search("주민세", regions=["아산시"], min_score=-1), [])

    def test_body_changed_during_embedding_is_not_saved(self):
        import hashlib

        from sources.models import SourceChunk, SourceDocument

        doc = self.pending()
        before = self.row(doc)

        def approve_new_body():
            SourceDocument.objects.filter(pk=doc.pk).update(content_hash=hashlib.sha256(b"new").hexdigest())

        self.assertEqual(self.run_while(approve_new_body), {**STATS0, "changed": 1})
        self.assertFalse(SourceChunk.objects.filter(source_document=doc).exists())
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.ai_reprocessing_required, doc.version),
                         ("PENDING", True, before["version"]))  # 완료·전환 안 함

    def test_skip_path_completes_and_activates(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceDocument

        doc = self.source("a")  # 현재 본문 청크가 이미 있음
        SourceDocument.objects.filter(pk=doc.pk).update(ai_reprocessing_required=True, status="PENDING")
        before = self.row(doc)
        with mock.patch("ai.chatbot.indexing.embed_passages") as embed:
            self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1, "completed": 1, "activated": 1})
        embed.assert_not_called()
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.ai_reprocessing_required, doc.version),
                         ("ACTIVE", False, before["version"] + 1))
        self.assert_only_completion_fields_changed(before, doc)

    def test_skip_path_checks_the_same_conditions(self):
        import hashlib

        from django.db.models import F

        from ai.chatbot import build_source_chunks
        from ai.chatbot.indexing import _has_current_chunks as real_has_current
        from sources.models import SourceDocument

        for path, change in (
            ("edited", {"title": "관리자가 고친 제목", "version": F("version") + 1}),
            ("off", {"status": "INACTIVE"}),
            ("revoked", {"review_status": "PENDING_REVIEW"}),
            ("body", {"content_hash": hashlib.sha256(b"new").hexdigest()}),
        ):
            with self.subTest(change=sorted(change)):
                doc = self.source(path)
                SourceDocument.objects.filter(pk=doc.pk).update(ai_reprocessing_required=True, status="PENDING")

                def check_then_change(*args):  # 처리 시작 값을 읽은 뒤, 완료 직전 잠금 전에 바뀜
                    found = real_has_current(*args)
                    SourceDocument.objects.filter(pk=doc.pk).update(**change)
                    return found

                with mock.patch("ai.chatbot.indexing._has_current_chunks", side_effect=check_then_change):
                    self.assertEqual(build_source_chunks([doc.pk]), {**STATS0, "skipped": 1, "changed": 1})
                doc.refresh_from_db()
                self.assertTrue(doc.ai_reprocessing_required)
                self.assertNotEqual(doc.status, "ACTIVE")
                if path == "edited":
                    self.assertEqual(doc.title, "관리자가 고친 제목")

    def test_already_active_stays_active_without_version_bump(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk, SourceDocument

        doc = self.source("a")  # ACTIVE·재처리 완료
        before = self.row(doc)
        self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1})
        self.assertEqual(self.row(doc), before)

        # 차원이 달라 청크만 새로 만드는 경우도 재처리 완료 기록·version은 그대로
        SourceChunk.objects.filter(source_document=doc).update(embedding=[0.1] * 768)
        self.assertEqual(build_source_chunks(), {**STATS0, "documents": 1, "chunks": 1})
        self.assertEqual(self.row(doc), before)

        # ACTIVE인데 재처리 대기였다면 완료만 하고 ACTIVE 유지 (전환 수에는 안 셈)
        SourceDocument.objects.filter(pk=doc.pk).update(ai_reprocessing_required=True)
        self.assertEqual(build_source_chunks(), {**STATS0, "skipped": 1, "completed": 1})
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.version), ("ACTIVE", before["version"] + 1))

    def test_embedding_failure_keeps_state(self):
        from ai.chatbot import build_source_chunks
        from sources.models import SourceChunk

        doc = self.pending()
        before = self.row(doc)
        with mock.patch("ai.chatbot.indexing.embed_passages", side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                build_source_chunks()
        self.assertEqual(self.row(doc), before)
        self.assertFalse(SourceChunk.objects.filter(source_document=doc).exists())

@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class VerifyInterpretationTests(AIEnvMixin, TestCase):
    """S2 verify의 C1 항목별 형식 (DOCUMENTS_VERIFY_MODE=rag)."""

    ITEM_FIELDS = {"key", "label", "docValue", "officialValue", "status", "reason", "sourceIds"}
    EMPTY = {"overall": "UNVERIFIED", "status": "UNVERIFIED", "items": [], "sources": []}
    KEYS = ["AMOUNT", "DEADLINE", "PAYMENT_METHOD", "AGENCY_CONTACT"]

    def verify(self, result=None, llm=None):
        from ai.chatbot import verify_interpretation

        result = C1_RESULT if result is None else result
        if llm is None:
            return verify_interpretation(result)
        with mock.patch("ai.chatbot.llm.MockLLM.complete_json", return_value=llm):
            return verify_interpretation(result)

    @staticmethod
    def llm_items(keys, statuses):
        return {"items": [
            {"key": k, "status": s, "officialValue": None if s == "UNVERIFIED" else "공식 값",
             "reason": "이유", "sourceIds": [] if s == "UNVERIFIED" else ["s1"]}
            for k, s in zip(keys, statuses)
        ]}

    def assert_status_is_overall(self, out):
        self.assertEqual(out["status"], out["overall"])

    def test_c1_shape_items_and_sources(self):
        import datetime

        good = self.source("good")
        good.published_at = datetime.date(2026, 3, 2)
        good.save(update_fields=["published_at"])
        province = self.source("province", region="충청남도", host="www.chungnam.go.kr")
        out = self.verify()

        self.assertEqual(set(out), {"overall", "status", "items", "sources"})
        self.assertEqual(out["overall"], "MATCHED")  # mock: 모든 항목 MATCHED
        self.assert_status_is_overall(out)
        self.assertEqual([i["key"] for i in out["items"]], self.KEYS)
        for item in out["items"]:
            self.assertEqual(set(item), self.ITEM_FIELDS)
            self.assertEqual(item["status"], "MATCHED")
        values = {i["key"]: i["docValue"] for i in out["items"]}
        self.assertEqual(values, {
            "AMOUNT": "6,250원", "DEADLINE": "납부 마감 2026-08-31",
            "PAYMENT_METHOD": "은행 창구, 가상계좌, 위택스", "AGENCY_CONTACT": "041-000-0000",
        })
        # sources: 결과에 한 번만, id s1·s2…, publishedAt은 있을 때만
        self.assertEqual([s["id"] for s in out["sources"]], ["s1", "s2"])
        by_doc = {s["sourceDocumentId"]: s for s in out["sources"]}
        self.assertEqual(set(by_doc), {good.pk, province.pk})
        self.assertEqual(by_doc[good.pk]["publishedAt"], "2026-03-02")
        self.assertNotIn("publishedAt", by_doc[province.pk])
        self.assertEqual(set(by_doc[province.pk]), {"id", "title", "url", "checkedAt", "sourceDocumentId"})
        self.assertRegex(by_doc[good.pk]["checkedAt"], r"^\d{4}-\d{2}-\d{2}$")
        ids = {s["id"] for s in out["sources"]}
        for item in out["items"]:
            self.assertTrue(item["sourceIds"] and set(item["sourceIds"]) <= ids)

    def test_sources_exclude_ineligible_and_max_ten(self):
        excluded = [
            self.source("pending", status="PENDING"),
            self.source("review", review="PENDING_REVIEW"),
            self.source("reproc", status="PENDING", reprocessing=True, index=False),
            self.source("nocheck", collected=False),
            self.source("cheonan", region="천안시"),
            self.source("unofficial", host="example.com"),
        ]
        for i in range(12):
            self.source(f"n{i}", text=ASAN_TEXT + ("\n\n# 추가\n" + "납부 안내 " * 120) * 3)
        sources = self.verify()["sources"]
        self.assertEqual([s["id"] for s in sources], [f"s{i}" for i in range(1, 11)])
        doc_ids = [s["sourceDocumentId"] for s in sources]
        self.assertEqual(len(set(doc_ids)), 10)
        for d in excluded:
            self.assertNotIn(d.pk, doc_ids)

    def test_items_without_document_value_are_excluded(self):
        self.source("a")
        # 지금 더미 interpret처럼 amount·payment_methods가 없으면 → 2개만
        out = self.verify(RESULT)
        self.assertEqual([i["key"] for i in out["items"]], ["DEADLINE", "AGENCY_CONTACT"])
        out = self.verify({**C1_RESULT, "amount": None, "payment_methods": [" ", ""], "agency": {"name": "아산시청"}})
        self.assertEqual([i["key"] for i in out["items"]], ["DEADLINE"])
        # 대조할 값이 하나도 없으면 검색·LLM 없이 항목 0개 → UNVERIFIED
        with mock.patch("ai.chatbot.integration.search") as search, \
                mock.patch("ai.chatbot.llm.MockLLM.complete_json") as llm:
            out = self.verify({**C1_RESULT, "amount": "", "payment_methods": [], "deadlines": [],
                               "agency": {"name": "아산시청"}})
        self.assertEqual(out, self.EMPTY)
        search.assert_not_called()
        llm.assert_not_called()

    def test_payment_methods_string_or_wrong_type(self):
        self.source("a")

        def method_item(value):
            out = self.verify({**C1_RESULT, "payment_methods": value})
            return next((i for i in out["items"] if i["key"] == "PAYMENT_METHOD"), None)

        # 문자열 하나는 글자 단위로 쪼개지 않고 그대로
        self.assertEqual(method_item("위택스")["docValue"], "위택스")
        self.assertEqual(method_item(" 위택스 ")["docValue"], "위택스")
        # 리스트는 지금처럼: 빈 값·비문자열은 거르고 중복 제거
        self.assertEqual(method_item(["위택스", "", 3, None, "위택스", "가상계좌"])["docValue"], "위택스, 가상계좌")
        # 숫자·dict 같은 이상한 타입이나 빈 문자열이면 항목 제외
        for value in (123, 1.5, True, {"name": "위택스"}, None, "", "  "):
            with self.subTest(value=value):
                self.assertIsNone(method_item(value))

    def test_overall_rules(self):
        from ai.chatbot.integration import _overall

        def items(*statuses):
            return [{"status": s} for s in statuses]

        self.assertEqual(_overall(items("MATCHED", "MISMATCH", "UNVERIFIED")), "MISMATCH")
        self.assertEqual(_overall(items("MATCHED", "MATCHED")), "MATCHED")
        self.assertEqual(_overall(items("MATCHED", "UNVERIFIED")), "UNVERIFIED")
        self.assertEqual(_overall([]), "UNVERIFIED")

        self.source("a")
        cases = {
            "MISMATCH": ["MATCHED", "MISMATCH", "UNVERIFIED", "MATCHED"],
            "MATCHED": ["MATCHED"] * 4,
            "UNVERIFIED": ["MATCHED", "UNVERIFIED", "MATCHED", "MATCHED"],
        }
        for expected, statuses in cases.items():
            out = self.verify(llm=self.llm_items(self.KEYS, statuses))
            self.assertEqual([i["status"] for i in out["items"]], statuses)
            self.assertEqual(out["overall"], expected)
            self.assert_status_is_overall(out)

    def test_bad_llm_item_becomes_unverified(self):
        self.source("a")
        llm = {"items": [
            {"key": "AMOUNT", "status": "maybe", "officialValue": "6,250원", "reason": "?", "sourceIds": ["s1"]},
            {"key": "DEADLINE", "status": "MATCHED", "officialValue": "8월 31일", "reason": "x", "sourceIds": ["s9"]},
            {"key": "PAYMENT_METHOD", "status": "MATCHED", "officialValue": "위택스", "reason": "x", "sourceIds": []},
            {"key": "AGENCY_CONTACT", "status": "MISMATCH", "officialValue": "041-537-3001",
             "reason": "공식 번호와 다름", "sourceIds": ["s1", "S1"]},
        ]}
        out = self.verify(llm=llm)
        items = {i["key"]: i for i in out["items"]}
        for key in ("AMOUNT", "DEADLINE", "PAYMENT_METHOD"):  # 이상한 status / 없는 id / 근거 없는 판정
            self.assertEqual(items[key]["status"], "UNVERIFIED", key)
            self.assertIsNone(items[key]["officialValue"])
            self.assertEqual(items[key]["sourceIds"], [])
        self.assertEqual(items["AGENCY_CONTACT"]["status"], "MISMATCH")
        self.assertEqual(items["AGENCY_CONTACT"]["sourceIds"], ["s1"])  # 대소문자 정리·중복 제거
        self.assertEqual(out["overall"], "MISMATCH")
        self.assert_status_is_overall(out)

        # LLM이 항목을 빠뜨리거나 형식이 아예 틀리면 → 그 항목(들) UNVERIFIED, 출처 목록은 유지
        for llm in ({"items": [{"key": "DEADLINE", "status": "MATCHED", "officialValue": "v", "reason": "r",
                                "sourceIds": ["s1"]}]}, {"status": "MATCHED"}, {"items": "x"}, ["x"]):
            out = self.verify(llm=llm)
            statuses = {i["key"]: i["status"] for i in out["items"]}
            self.assertEqual(statuses["AMOUNT"], "UNVERIFIED")
            self.assertEqual(out["overall"], "UNVERIFIED")
            self.assertTrue(out["sources"])

    def test_unidentified_agency_or_no_sources(self):
        self.source("a")
        out = self.verify({**C1_RESULT, "agency": {"name": "", "phone": "041-000-0000"}})
        self.assertEqual(out["sources"], [])
        self.assertEqual(len(out["items"]), 4)
        self.assertTrue(all(i["status"] == "UNVERIFIED" and "발신기관" in i["reason"] for i in out["items"]))
        self.assertEqual(out["overall"], "UNVERIFIED")
        with mock.patch("ai.chatbot.integration.search", return_value=[]):
            out = self.verify()
        self.assertTrue(all("찾지 못했어요" in i["reason"] for i in out["items"]))
        self.assert_status_is_overall(out)

    def test_errors_are_caught_in_verify_interpretation(self):
        self.source("a")
        for target in ("ai.chatbot.integration.search", "ai.chatbot.llm.MockLLM.complete_json",
                       "ai.chatbot.integration.normalize_document"):
            with mock.patch(target, side_effect=RuntimeError("문서 내용 포함 오류")), \
                    self.assertLogs("ai.chatbot.integration", "WARNING") as logs:
                self.assertEqual(self.verify(), self.EMPTY)
            self.assertNotIn("문서 내용", "\n".join(logs.output))

    @override_settings(DOCUMENTS_VERIFY_MODE="rag", DOCUMENTS_STEP_DELAY=0)
    def test_llm_error_keeps_document_processing(self):
        from django.contrib.auth import get_user_model

        from documents.models import Document, InterpretationResult
        from documents.services import pipeline

        self.source("a")
        user = get_user_model().objects.create_user("verify-fail@example.com")
        document = Document.objects.create(owner=user, file="documents/t.pdf", file_name="t.pdf", file_type="PDF")
        with mock.patch("ai.chatbot.llm.MockLLM.complete_json", side_effect=TimeoutError):
            self.assertTrue(pipeline.process_document(document.pk))
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.DONE)
        self.assertEqual(InterpretationResult.objects.get(document=document).verification, self.EMPTY)

    @override_settings(DOCUMENTS_VERIFY_MODE="rag")
    def test_pipeline_slot_uses_rag(self):
        from documents.services import pipeline

        doc = self.source("a")
        out = pipeline.verify(RESULT)
        self.assertEqual(set(out), {"overall", "status", "items", "sources"})
        self.assertEqual(out["sources"][0]["sourceDocumentId"], doc.pk)

    def test_pipeline_slot_default_is_dummy(self):
        from documents.services import pipeline

        with mock.patch("ai.chatbot.integration.verify_interpretation") as rag:
            out = pipeline.verify(RESULT)
        rag.assert_not_called()
        self.assertNotIn("sourceDocumentId", out["sources"][0])

    def test_chatbot_official_sources_follow_item_source_ids(self):
        """실제 verify 결과(rag, mock LLM)를 그대로 chat_service에 넣으면 MATCHED·MISMATCH 항목의 출처만 공식 출처가 된다 (#65).

        items[].sourceIds → verification.sources[].id → sourceDocumentId 연결을 끝까지 확인한다.
        """
        from chatbot.services.chat_service import _official_sources

        docs = [self.source(p) for p in ("a", "b", "c", "d")]
        llm = {"items": [
            {"key": "AMOUNT", "status": "MATCHED", "officialValue": "6,250원", "reason": "같음", "sourceIds": ["s1"]},
            {"key": "DEADLINE", "status": "MISMATCH", "officialValue": "2026-09-30", "reason": "다름",
             "sourceIds": ["S2"]},  # 대소문자는 맞춰 줌
            # UNVERIFIED 항목이 인용한 id는 버림 → s3는 근거가 아님
            {"key": "PAYMENT_METHOD", "status": "UNVERIFIED", "officialValue": None, "reason": "없음",
             "sourceIds": ["s3"]},
            # 없는 id(s9)를 섞으면 그 항목은 UNVERIFIED → s4도 근거가 아님
            {"key": "AGENCY_CONTACT", "status": "MATCHED", "officialValue": "041", "reason": "같음",
             "sourceIds": ["s4", "s9"]},
        ]}
        verification = self.verify(llm=llm)
        by_id = {s["id"]: s["sourceDocumentId"] for s in verification["sources"]}
        self.assertEqual(sorted(by_id.values()), sorted(d.pk for d in docs))  # 검색된 4개 모두 sources에는 있음
        self.assertEqual([(i["key"], i["status"], i["sourceIds"]) for i in verification["items"]], [
            ("AMOUNT", "MATCHED", ["s1"]), ("DEADLINE", "MISMATCH", ["s2"]),
            ("PAYMENT_METHOD", "UNVERIFIED", []), ("AGENCY_CONTACT", "UNVERIFIED", []),
        ])
        self.assertEqual(verification["overall"], "MISMATCH")

        linked = [s["sourceDocumentId"] for s in _official_sources({"verification": verification})]
        self.assertEqual(sorted(linked), sorted([by_id["s1"], by_id["s2"]]))

        # 모두 MATCHED여도 sources 전체가 아니라 인용된 출처만
        all_s1 = self.verify(llm=self.llm_items(self.KEYS, ["MATCHED"] * 4))
        self.assertEqual([s["sourceDocumentId"] for s in _official_sources({"verification": all_s1})],
                         [next(s["sourceDocumentId"] for s in all_s1["sources"] if s["id"] == "s1")])
        # 모든 항목이 UNVERIFIED면 공식 출처 없음
        none = self.verify(llm=self.llm_items(self.KEYS, ["UNVERIFIED"] * 4))
        self.assertEqual(_official_sources({"verification": none}), [])

    def test_output_passes_result_api_normalization_unchanged(self):
        """성진님 결과 API·GET /verification/(normalize_verification, #67)이 내 출력을 고치거나 낮추지 않는지."""
        import datetime

        from documents.serializers import normalize_verification

        good = self.source("a")
        good.published_at = datetime.date(2026, 7, 1)
        good.save(update_fields=["published_at"])
        self.source("b")
        bad_llm = {"items": [{"key": "AMOUNT", "status": "MATCHED", "officialValue": 6250, "reason": None,
                              "sourceIds": ["s1"]}]}
        for out in (self.verify(), self.verify(llm=self.llm_items(self.KEYS, ["MATCHED", "MISMATCH"] * 2)),
                    self.verify(llm=bad_llm), self.verify(RESULT), self.verify({"agency": {"name": "아산시청"}}),
                    self.verify({**C1_RESULT, "agency": {"name": "어딘가"}, "region": None})):
            with self.subTest(overall=out["overall"], n=len(out["items"])):
                self.assertEqual(normalize_verification(out), out)

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
