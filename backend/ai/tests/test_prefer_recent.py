"""S5 3A: 같은 내용의 공식 자료가 여러 개면 최근 자료(확인일, 없으면 기준일)를 먼저 쓴다."""
import unittest
from datetime import date
from unittest import mock

from ai.chatbot import prompts
from ai.chatbot.retriever import SearchResult
from ai.chatbot.service import answer_with_results, normalize_document, prefer_recent


def result(title, agency="아산시청", *, checked=None, published=None, doc_id=1, score=0.8, text="본문"):
    return SearchResult(doc_id, text, score, f"https://www.asan.go.kr/{doc_id}", title, agency, "아산시",
                        published, checked, doc_id)


class PreferRecentTests(unittest.TestCase):
    def test_same_agency_and_title_puts_recent_first(self):
        old = result("주민세 납부 안내", checked=date(2026, 1, 5), doc_id=1)
        new = result("주민세 납부 안내", checked=date(2026, 10, 1), doc_id=2)
        self.assertEqual(prefer_recent([old, new]), [new, old])

    def test_very_similar_title_is_same_group(self):
        old = result("2026년 주민세(개인분) 납부 안내", checked=date(2026, 1, 5), doc_id=1)
        new = result("2026년 주민세 (개인분) 납부안내.", checked=date(2026, 10, 1), doc_id=2)  # 공백·기호만 다름
        self.assertEqual(prefer_recent([old, new]), [new, old])
        typo = result("2026년 주민세(개인분) 납부 안내문", checked=date(2026, 10, 1), doc_id=3)  # 한 글자 더
        self.assertEqual(prefer_recent([old, typo]), [typo, old])

    def test_different_agency_or_title_is_not_grouped(self):
        """보수적으로: 기관이 다르거나 제목이 꽤 다르면 묶지 않고 관련도 순 그대로."""
        old = result("주민세 납부 안내", checked=date(2026, 1, 5), doc_id=1)
        cases = [
            result("주민세 납부 안내", agency="천안시청", checked=date(2026, 10, 1), doc_id=2),
            result("자동차세 납부 안내", checked=date(2026, 10, 1), doc_id=3),
            result("주민세 감면 신청 방법", checked=date(2026, 10, 1), doc_id=4),
            result("주민세 납부 안내", agency=None, checked=date(2026, 10, 1), doc_id=5),
            result(None, checked=date(2026, 10, 1), doc_id=6),
        ]
        for other in cases:
            with self.subTest(other=(other.agency, other.title)):
                self.assertEqual(prefer_recent([old, other]), [old, other])

    def test_published_at_when_checked_at_missing(self):
        old = result("주민세 납부 안내", published=date(2025, 7, 1), doc_id=1)
        new = result("주민세 납부 안내", published=date(2026, 7, 1), doc_id=2)
        none = result("주민세 납부 안내", doc_id=3)  # 날짜 둘 다 없으면 가장 뒤
        self.assertEqual(prefer_recent([none, old, new]), [new, old, none])
        # 확인일이 있으면 기준일 대신 확인일로 비교 (기준일 2020이어도 확인일 2026-09가 2026-07보다 최근)
        checked = result("주민세 납부 안내", checked=date(2026, 9, 1), published=date(2020, 1, 1), doc_id=4)
        self.assertEqual(prefer_recent([new, checked]), [checked, new])
        stale = result("주민세 납부 안내", checked=date(2026, 3, 1), published=date(2026, 12, 1), doc_id=5)
        self.assertEqual(prefer_recent([stale, new]), [new, stale])  # 기준일이 더 최근이어도 확인일 2026-03으로 비교

    def test_group_keeps_its_relevance_position(self):
        """묶음 자리는 관련도가 가장 높은 자료가 있던 곳. 다른 자료 순서는 그대로."""
        a_old = result("주민세 납부 안내", checked=date(2026, 1, 1), doc_id=1)
        other = result("지방세 납부 방법", checked=date(2026, 5, 1), doc_id=2)
        a_new = result("주민세 납부 안내", checked=date(2026, 9, 1), doc_id=3)
        tie = result("주민세 납부 안내", checked=date(2026, 9, 1), doc_id=4)  # 같은 날짜는 원래 순서
        self.assertEqual(prefer_recent([a_old, other, a_new, tie]), [a_new, tie, a_old, other])
        self.assertEqual(prefer_recent([]), [])

    def test_answer_prompt_and_sources_use_recent_first(self):
        """answer_with_results(): 최근 자료가 S1이 되고, LLM이 S1을 쓰면 그 자료가 출처가 된다."""
        old = result("주민세 납부 안내", checked=date(2026, 1, 5), doc_id=1, score=0.9, text="기한 8월 31일 (옛 자료)")
        new = result("주민세 납부 안내", checked=date(2026, 10, 1), doc_id=2, score=0.8, text="기한 9월 30일 (새 자료)")
        llm = mock.Mock()
        # 한국어 질문은 검색어 재작성 없이 바로 검색하므로 LLM 호출은 답변 1번
        llm.complete_json.return_value = {"answer": "답", "status": "GROUNDED", "used_sources": ["S1"],
                                          "suggested_questions": []}
        doc = normalize_document({"title": "주민세 고지서", "agency": {"name": "아산시청"}, "language": "ko"})
        with mock.patch("ai.chatbot.service.search", return_value=[old, new]), \
                mock.patch("ai.chatbot.service.embed_query", return_value=[0.0]), \
                mock.patch("ai.chatbot.service.get_llm", return_value=llm):
            _, status, used, _, _ = answer_with_results(doc, "언제까지 내요?", official_ids={1, 2})
        user_msg = llm.complete_json.call_args_list[-1].args[1][-1]["content"]
        self.assertLess(user_msg.index("새 자료"), user_msg.index("옛 자료"))
        self.assertIn("<S1 | 주민세 납부 안내 | 아산시청 | 지역: 아산시 | 확인일: 2026-10-01", user_msg)
        self.assertEqual((status.value, [r.source_document_id for r in used]), ("GROUNDED", [2]))

    def test_prompt_tells_to_prefer_recent_and_notice_difference(self):
        text = prompts.ANSWER_SYSTEM.format(lang="한국어")
        rule = text[text.index("5. CONFLICT"):text.index("6. ")]
        for phrase in ("더 최근인 자료", "확인일", "기준일", "다르다는 사실", "담당기관 확인"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rule)


if __name__ == "__main__":
    unittest.main()
