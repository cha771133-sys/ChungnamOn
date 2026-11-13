"""S4 체크리스트·전화 대본 (ai.chatbot.checklist.build_actions). manage.py test로 돈다."""
import re
import unittest
from unittest import mock

from ai.tests.test_integration import DJANGO, AIEnvMixin, TestCase, override_settings

RESULT = {
    "doc_type": "고지서", "title": "주민세 납부 안내", "original_text": "주민세 6,250원 납부기한 8월 31일",
    "summary": ["2026년 8월 31일까지 주민세를 내야 합니다."], "terms": [],
    "deadlines": [{"label": "납부 마감", "date": "2026-08-31"}, {"label": "감면 신청", "date": "2026-07-31"}],
    "required_docs": ["고지서", "외국인등록증"],
    "agency": {"name": "아산시청 세무과", "phone": "041-000-0000", "source": "document"},
    "amount": "6,250원", "payment_methods": ["은행 창구", "가상계좌", "위택스"],
    "language": "vi",
    # interpret가 준 checklist. rag에서는 무시, dummy에서는 그대로
    "checklist": [{"text": "납부 영수증 보관하기", "state": "CONFIRMED", "sourceIds": []}],
}
SOURCES = [{"id": f"s{i}", "title": f"자료 {i}", "url": "https://www.asan.go.kr/", "checkedAt": "2026-10-30"}
           for i in range(1, 5)]


def verification(statuses: dict, ids: dict | None = None, sources=None) -> dict:
    """{key: status} → C1 verification. ids로 항목별 sourceIds를 정함 (기본: MATCHED·MISMATCH면 s1)."""
    ids = ids or {}
    items = [{"key": k, "label": k, "docValue": "v", "officialValue": None if s == "UNVERIFIED" else "o",
              "status": s, "reason": "r", "sourceIds": ids.get(k, [] if s == "UNVERIFIED" else ["s1"])}
             for k, s in statuses.items()]
    overall = "MISMATCH" if "MISMATCH" in statuses.values() else (
        "MATCHED" if items and all(s == "MATCHED" for s in statuses.values()) else "UNVERIFIED")
    return {"overall": overall, "status": overall, "items": items, "sources": SOURCES if sources is None else sources}


ALL_MATCHED = {"AMOUNT": "MATCHED", "DEADLINE": "MATCHED", "PAYMENT_METHOD": "MATCHED", "AGENCY_CONTACT": "MATCHED"}


DEFAULT = object()


class ChecklistBase(AIEnvMixin):
    def build(self, result=None, v=DEFAULT, *, llm=None, language="ko"):
        """llm: None이면 LLM 응답 없음({}), 예외 인스턴스면 실패, dict면 그 응답."""
        from ai.chatbot import build_actions

        result = RESULT if result is None else result
        v = verification(ALL_MATCHED) if v is DEFAULT else v
        kwargs = {"side_effect": llm} if isinstance(llm, Exception) else {"return_value": llm or {}}
        with mock.patch("ai.chatbot.llm.MockLLM.complete_json", **kwargs) as complete:
            out = build_actions(result, v, language)
        self.complete = complete
        return out

    @staticmethod
    def by_text(out):
        return {i["text"]: i for i in out["checklist"]}


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
@override_settings(DOCUMENTS_CHECKLIST_MODE="rag")
class ChecklistRulesTests(ChecklistBase, TestCase):

    def test_four_item_kinds(self):
        v = verification({**ALL_MATCHED, "AGENCY_CONTACT": "MISMATCH"}, ids={"AGENCY_CONTACT": ["s2"]})
        out = self.build(v=v)
        self.assertEqual([i["text"] for i in out["checklist"]], [
            "2026년 8월 31일까지 6,250원 내기",            # ① 납부 기한 + 금액 + 납부 방법
            "2026년 7월 31일까지 감면 신청 확인하기",       # ② 그 밖의 기한
            "고지서 준비하기", "외국인등록증 준비하기",      # ③ 준비서류
            "아산시청 세무과 연락처 확인하기",              # ④ 연락처 (MISMATCH일 때만)
        ])
        pay, other, doc1, doc2, agency = out["checklist"]
        self.assertEqual((pay["due"], pay["method"], pay["docs"]), ("2026-08-31", "은행 창구, 가상계좌, 위택스", []))
        self.assertEqual((other["due"], doc2["docs"]), ("2026-07-31", ["외국인등록증"]))
        self.assertEqual(set(pay), {"text", "detail", "docs", "method", "due", "state", "note", "sourceIds"})
        self.assertNotIn("041-000-0000", json_dump(out["checklist"]))  # 연락처 번호는 항목에 안 씀
        # interpret의 checklist는 rag에서 무시
        self.assertNotIn("납부 영수증 보관하기", [i["text"] for i in out["checklist"]])

    def test_items_pass_documents_clean_checklist_unchanged(self):
        """성진님 #69 clean_checklist가 항목을 빼거나 state를 바꾸지 않는지."""
        from documents.services.checklist import clean_checklist

        out = self.build(v=verification({**ALL_MATCHED, "AGENCY_CONTACT": "UNVERIFIED"}))
        cleaned = clean_checklist(out["checklist"])
        self.assertEqual(len(cleaned), len(out["checklist"]))
        for raw, saved in zip(out["checklist"], cleaned):
            self.assertEqual((saved["text"], saved["state"], saved["source_ids"], saved["docs"]),
                             (raw["text"], raw["state"], raw["sourceIds"], raw["docs"]))
            self.assertEqual(saved["due"] and saved["due"].isoformat(), raw["due"])

    def test_no_value_no_item(self):
        texts = lambda out: [i["text"] for i in out["checklist"]]  # noqa: E731
        base = {**RESULT, "deadlines": [], "required_docs": [], "amount": "", "payment_methods": [],
                "agency": {"name": "아산시청"}}
        self.assertEqual(texts(self.build(base)), [])
        self.assertEqual(texts(self.build({**base, "amount": "6,250원"})), ["6,250원 내기"])
        self.assertEqual(texts(self.build({**base, "payment_methods": "위택스"})), ["납부하기"])
        self.assertEqual(texts(self.build({**base, "deadlines": [{"label": "납부 기한", "date": "2026-08-31"}]})),
                         ["2026년 8월 31일까지 내기"])
        # 날짜 없는 기한·빈 서류 이름은 무시
        self.assertEqual(texts(self.build({**base, "deadlines": [{"label": "신고"}], "required_docs": [" ", 3]})), [])
        # 연락처가 공식 자료와 맞으면 확인 항목 없음, 번호가 없어도 없음
        self.assertEqual(texts(self.build({**base, "agency": {"name": "세무과", "phone": "041"}})), [])
        self.assertEqual(texts(self.build({**base, "agency": {"name": "세무과"}},
                                          verification({"AGENCY_CONTACT": "UNVERIFIED"}))), [])

    def test_state_rules(self):
        def pay_state(statuses):
            return self.build(v=verification(statuses))["checklist"][0]["state"]

        self.assertEqual(pay_state(ALL_MATCHED), "CONFIRMED")
        self.assertEqual(pay_state({**ALL_MATCHED, "AMOUNT": "UNVERIFIED"}), "CHECK_NEEDED")
        self.assertEqual(pay_state({**ALL_MATCHED, "PAYMENT_METHOD": "MISMATCH"}), "CHECK_NEEDED")
        self.assertEqual(pay_state({"DEADLINE": "MATCHED", "PAYMENT_METHOD": "MATCHED"}), "CHECK_NEEDED")  # AMOUNT 없음
        self.assertEqual(pay_state({**ALL_MATCHED, "AMOUNT": "WEIRD"}), "CHECK_NEEDED")
        # verify 결과가 없거나 옛 형식이면 모두 CHECK_NEEDED, 준비서류는 늘 CHECK_NEEDED
        for v in ({}, None, {"status": "MATCHED", "sources": []}):
            out = self.build(v=v)
            self.assertEqual({i["state"] for i in out["checklist"]}, {"CHECK_NEEDED"})
        docs = [i for i in self.build()["checklist"] if i["docs"]]
        self.assertEqual({i["state"] for i in docs}, {"CHECK_NEEDED"})

    def test_source_ids_only_matched_mismatch_and_existing(self):
        v = verification(
            {"AMOUNT": "MATCHED", "DEADLINE": "UNVERIFIED", "PAYMENT_METHOD": "MATCHED", "AGENCY_CONTACT": "MISMATCH"},
            ids={"AMOUNT": ["s1"], "DEADLINE": ["s2"], "PAYMENT_METHOD": ["s9", "s3", "s1"], "AGENCY_CONTACT": ["s4"]},
        )
        items = self.by_text(self.build(v=v))
        self.assertEqual(items["2026년 8월 31일까지 6,250원 내기"]["sourceIds"], ["s1", "s3"])  # s2(UNVERIFIED)·s9(없음) 제외
        self.assertEqual(items["2026년 7월 31일까지 감면 신청 확인하기"]["sourceIds"], [])
        self.assertEqual(items["아산시청 세무과 연락처 확인하기"]["sourceIds"], ["s4"])
        self.assertEqual(items["고지서 준비하기"]["sourceIds"], [])


def json_dump(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
@override_settings(DOCUMENTS_CHECKLIST_MODE="rag")
class ChecklistLLMTests(ChecklistBase, TestCase):
    """번역 + CONDITIONAL 판정 (LLM 한 번). 공식 자료 본문은 verification.sources의 sourceDocumentId로 읽는다."""

    def setUp(self):
        super().setUp()
        self.docs = [self.source(p) for p in ("a", "b")]
        self.linked_sources = [{**SOURCES[0], "sourceDocumentId": self.docs[0].pk},
                               {**SOURCES[1], "sourceDocumentId": self.docs[1].pk}]

    def v(self, statuses=None, **kw):
        return verification(statuses or ALL_MATCHED, sources=self.linked_sources, **kw)

    @staticmethod
    def llm(*entries):
        return {"items": [dict(e) for e in entries]}

    def test_llm_gets_source_texts_and_one_call(self):
        self.build(v=self.v(), language="vi")
        self.assertEqual(self.complete.call_count, 1)
        system, messages = self.complete.call_args.args
        self.assertIn("Tiếng Việt", system)
        self.assertIn("<s1>", messages[0]["content"])
        self.assertIn("주민세", messages[0]["content"])  # 공식 자료 본문
        self.assertNotIn("<s3>", messages[0]["content"])  # sourceDocumentId 없는 출처는 본문 없음

    def test_only_downgrade_to_conditional(self):
        v = self.v({**ALL_MATCHED, "AGENCY_CONTACT": "MISMATCH"}, ids={"AGENCY_CONTACT": ["s2"]})
        out = self.build(v=v, llm=self.llm(
            {"index": 0, "state": "CONDITIONAL", "note": "가상계좌로 내면 고지서가 없어도 돼요.", "sourceIds": ["s2"]},
            {"index": 1, "state": "CONDITIONAL", "note": "", "sourceIds": ["s1"]},       # note 없음 → 그대로
            {"index": 2, "state": "CONFIRMED", "sourceIds": ["s1"]},                       # 서류: 근거 있으면 CONFIRMED
            {"index": 3, "state": "CONFIRMED", "sourceIds": ["s9"]},                       # 없는 id → 그대로
            {"index": 4, "state": "CONFIRMED", "sourceIds": ["s1"]},                       # CHECK_NEEDED는 올리지 않음
        ))
        pay, other, doc1, doc2, agency = out["checklist"]
        self.assertEqual((pay["state"], pay["note"], pay["sourceIds"]),
                         ("CONDITIONAL", "가상계좌로 내면 고지서가 없어도 돼요.", ["s1", "s2"]))
        self.assertEqual((other["state"], doc1["state"], doc1["sourceIds"]), ("CONFIRMED", "CONFIRMED", ["s1"]))
        self.assertEqual((doc2["state"], agency["state"]), ("CHECK_NEEDED", "CHECK_NEEDED"))

        # verify가 CHECK_NEEDED로 둔 항목은 CONDITIONAL로도 못 바꿈, 근거 id 없이 낮추기도 안 됨
        out = self.build(v=self.v({**ALL_MATCHED, "AMOUNT": "UNVERIFIED"}), llm=self.llm(
            {"index": 0, "state": "CONDITIONAL", "note": "조건", "sourceIds": ["s1"]},
            {"index": 1, "state": "CONDITIONAL", "note": "조건", "sourceIds": []},
        ))
        self.assertEqual([i["state"] for i in out["checklist"][:2]], ["CHECK_NEEDED", "CONFIRMED"])

    def test_conditional_doc_needs_note(self):
        out = self.build(v=self.v(), llm=self.llm(
            {"index": 2, "state": "CONDITIONAL", "note": "", "sourceIds": ["s1"]},
            {"index": 3, "state": "CONDITIONAL", "note": "은행 창구에서만 필요해요.", "sourceIds": ["s1"]},
        ))
        self.assertEqual([i["state"] for i in out["checklist"][2:4]], ["CHECK_NEEDED", "CONDITIONAL"])

    def test_translation_keeps_amount_digits(self):
        good = {"index": 0, "text": "Nộp 6.250 won trước ngày 31/8/2026", "detail": "Hạn nộp: 31/8/2026",
                "method": "Quầy ngân hàng, tài khoản ảo, Wetax"}
        changed = {"index": 1, "text": "Kiểm tra đơn giảm thuế trước ngày 30/7/2026"}  # 31 → 30
        out = self.build(v=self.v(), language="vi", llm=self.llm(
            good, changed, {"index": 2, "text": "Chuẩn bị giấy báo thuế"}))
        pay, other, doc1 = out["checklist"][:3]
        self.assertEqual((pay["text"], pay["method"]), (good["text"], good["method"]))
        self.assertEqual(other["text"], "2026년 7월 31일까지 감면 신청 확인하기")  # 숫자가 바뀌어 한국어 유지
        self.assertEqual(doc1["text"], "Chuẩn bị giấy báo thuế")
        self.assertEqual(out["checklist"][3]["text"], "외국인등록증 준비하기")  # LLM이 빠뜨린 항목은 한국어

        lost = self.build(v=self.v(), language="vi", llm=self.llm({"index": 0, "text": "Nộp thuế trước 31/8/2026"}))
        self.assertEqual(lost["checklist"][0]["text"], "2026년 8월 31일까지 6,250원 내기")  # 금액이 빠짐
        # 한국어 사용자면 LLM 문장으로 바꾸지 않음
        ko = self.build(v=self.v(), language="ko", llm=self.llm({"index": 2, "text": "다른 문장"}))
        self.assertEqual(ko["checklist"][2]["text"], "고지서 준비하기")

    def test_llm_failure_returns_korean_rule_items(self):
        expected = self.build(v=self.v(), llm={})["checklist"]
        with self.assertLogs("ai.chatbot.checklist", level="WARNING") as logs:
            out = self.build(v=self.v(), language="vi", llm=TimeoutError("주민세 6,250원 원문"))
        self.assertEqual(out["checklist"], expected)
        self.assertEqual(out["checklist"][0]["text"], "2026년 8월 31일까지 6,250원 내기")
        self.assertNotIn("6,250", "\n".join(logs.output))  # 오류 원문(문서 내용)은 로그에 안 남김
        # LLM이 이상한 모양을 내도 한국어 규칙 결과
        for bad in ({"items": "x"}, {"items": [{"index": "0", "text": "x"}]}, {"answer": "..."}):
            self.assertEqual(self.build(v=self.v(), language="vi", llm=bad)["checklist"], expected)
        self.assertTrue(out["call_script"])


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
class CallScriptAndModeTests(AIEnvMixin, TestCase):
    PRIVATE = [r"\*{2,}", r"가림\]", r"\d{2,4}-\d{3,4}-\d{4}", r"01[016789]\d{7,8}", r"\d{6}-?[1-8]\d{6}",
               r"\d{9,}", r"@"]

    def build(self, result, v, language="vi"):
        from ai.chatbot import build_actions

        with override_settings(DOCUMENTS_CHECKLIST_MODE="rag"), \
                mock.patch("ai.chatbot.llm.MockLLM.complete_json", return_value={}):
            return build_actions(result, v, language)

    def test_call_script_has_questions_amount_and_no_private_info(self):
        v = verification({**ALL_MATCHED, "DEADLINE": "UNVERIFIED", "AGENCY_CONTACT": "MISMATCH"})
        script = self.build(RESULT, v)["call_script"]
        self.assertIn("아산시청 세무과에서 보낸 「주민세 납부 안내」", script)
        self.assertIn("금액: 6,250원", script)
        self.assertIn("납부 마감: 2026년 8월 31일", script)
        self.assertIn("기한이 맞는지", script)
        self.assertIn("외국인등록증이 필요한지", script)
        self.assertIn("고지서가 필요한지", script)
        self.assertIn("연락처가 맞는지", script)
        self.assertNotIn("041-000-0000", script)

        leaky = {**RESULT, "title": "홍길동님 010-1234-5678 고지서", "doc_type": "고지서 ***",
                 "original_text": "주민등록번호 900101-1234567 계좌 123-456-789012",
                 "required_docs": ["통장 사본(110-123-456789)", "고지서"],
                 "payment_methods": ["가상계좌 56211234567890"],
                 "deadlines": [{"label": "[주소 가림] 납부", "date": "2026-08-31"}],
                 "agency": {"name": "세무과 041-111-2222", "phone": "041-111-2222"}}
        script = self.build(leaky, verification({}))["call_script"]
        for pattern in self.PRIVATE:
            self.assertIsNone(re.search(pattern, script), pattern)
        self.assertIn("받은 문서", script)
        self.assertIn("금액: 6,250원", script)

    def test_dummy_mode_keeps_interpret_checklist(self):
        from ai.chatbot import build_actions
        from documents.services.pipeline import interpret, verify

        result = interpret("주민세 6,250원", "vi")
        v = verify(result)
        for mode in (None, "dummy", ""):
            with self.subTest(mode=mode), override_settings(DOCUMENTS_CHECKLIST_MODE=mode), \
                    mock.patch("ai.chatbot.llm.MockLLM.complete_json") as llm:
                out = build_actions(result, v, result["language"])
                llm.assert_not_called()
                self.assertIs(out["checklist"], result["checklist"])
                updated = {**result, **out}
                self.assertEqual(updated["checklist"], result["checklist"])
                self.assertIn("주민세 납부 안내", out["call_script"])

    def test_pipeline_is_untouched_and_still_saves_interpret_checklist(self):
        """성진님이 한 줄을 넣기 전까지 pipeline은 지금처럼 interpret checklist를 저장."""
        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile

        from documents.models import Document
        from documents.services.pipeline import process_document

        user = get_user_model().objects.create_user(email="s4@example.com", password="pw-123456!")
        doc = Document.objects.create(owner=user, file=SimpleUploadedFile("a.png", b"x"))
        with override_settings(DOCUMENTS_STEP_DELAY=0, DOCUMENTS_CHECKLIST_MODE="rag"):
            process_document(doc.pk)
        texts = list(doc.checklist_items.values_list("text", flat=True))
        self.assertEqual(texts[0], "주민세 6,250원 내기")

    def test_never_raises(self):
        from ai.chatbot import build_actions

        for result, v in ((None, None), ({"deadlines": "x", "agency": "세무과"}, {"items": "x"}), ({}, [])):
            with override_settings(DOCUMENTS_CHECKLIST_MODE="rag"):
                out = build_actions(result, v, None)
            self.assertEqual(set(out), {"checklist", "call_script"})
            self.assertIsInstance(out["checklist"], list)


@unittest.skipUnless(DJANGO, "manage.py test로 실행")
@override_settings(DOCUMENTS_CHECKLIST_MODE="rag")
class ChecklistWithRealVerifyTests(AIEnvMixin, TestCase):
    """실제 verify_interpretation()(rag, mock LLM) 결과를 그대로 넣었을 때 state·sourceIds가 채워지는지 (#63)."""

    def run_both(self, verify_items=None):
        from ai.chatbot import build_actions, verify_interpretation
        from ai.chatbot.llm import MockLLM

        real = MockLLM.complete_json

        def fake(llm_self, system, messages, *, mock_hint=None, **kw):
            if (mock_hint or {}).get("task") == "verify" and verify_items is not None:
                return {"items": verify_items}
            return real(llm_self, system, messages, mock_hint=mock_hint, **kw)

        with mock.patch.object(MockLLM, "complete_json", fake):
            v = verify_interpretation(RESULT)
            return v, build_actions(RESULT, v, "vi")

    def test_all_matched_verify_confirms_payment(self):
        self.source("a")
        v, out = self.run_both()  # mock verify: 모든 항목 MATCHED, s1 근거
        self.assertEqual(v["overall"], "MATCHED")
        pay = out["checklist"][0]
        self.assertEqual((pay["text"], pay["state"], pay["sourceIds"]),
                         ("2026년 8월 31일까지 6,250원 내기", "CONFIRMED", ["s1"]))
        self.assertTrue(all(i in [s["id"] for s in v["sources"]] for i in pay["sourceIds"]))
        # 연락처가 MATCHED라 연락처 확인 항목은 없음, 준비서류는 근거가 없어 CHECK_NEEDED
        self.assertNotIn("연락처 확인하기", " ".join(i["text"] for i in out["checklist"]))
        self.assertEqual({i["state"] for i in out["checklist"] if i["docs"]}, {"CHECK_NEEDED"})

    def test_mismatch_and_unverified_items_from_verify(self):
        self.source("a"), self.source("b")
        v, out = self.run_both([
            {"key": "AMOUNT", "status": "MATCHED", "officialValue": "6,250원", "reason": "같음", "sourceIds": ["s1"]},
            {"key": "DEADLINE", "status": "UNVERIFIED", "officialValue": None, "reason": "없음", "sourceIds": []},
            {"key": "PAYMENT_METHOD", "status": "MATCHED", "officialValue": "위택스", "reason": "같음",
             "sourceIds": ["s2"]},
            {"key": "AGENCY_CONTACT", "status": "MISMATCH", "officialValue": "041-111-1111", "reason": "다름",
             "sourceIds": ["s2"]},
        ])
        self.assertEqual(v["overall"], "MISMATCH")
        items = {i["text"]: i for i in out["checklist"]}
        pay = items["2026년 8월 31일까지 6,250원 내기"]
        self.assertEqual((pay["state"], pay["sourceIds"]), ("CHECK_NEEDED", ["s1", "s2"]))  # DEADLINE 미확인
        agency = items["아산시청 세무과 연락처 확인하기"]
        self.assertEqual((agency["state"], agency["sourceIds"]), ("CHECK_NEEDED", ["s2"]))
        self.assertIn("기한이 맞는지", out["call_script"].replace("금액과 납부 기한", "기한이 맞는지"))
        self.assertNotIn("041-111-1111", out["call_script"])
