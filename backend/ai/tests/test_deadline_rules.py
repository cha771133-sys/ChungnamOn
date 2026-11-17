"""상대 기한 계산·판정 (verify DEADLINE, deadline_rules)."""
import unittest
from datetime import date

from ai.chatbot import deadline_rules as dr

SOURCE = "외국인등록\n이 민원은 입국한 날부터 90일 이내에 체류지를 관할하는 출입국·외국인관서장에게 신청해야 합니다."
ORIGINAL = "외국인등록 안내\n성명 [이름 가림]\n입국일 2026년 10월 5일\n신청 기한 2027년 1월 3일까지\n2026년 11월 10일"
DEADLINES = [{"label": "신청 기한", "date": "2027-01-03"}]


def rule(**kw):
    raw = {"base": "입국일", "baseLabels": ["입국일", "입국한 날"], "amount": 90, "unit": "day",
           "appliesTo": "신청 기한", "quote": "입국한 날부터 90일 이내에", "sourceId": "s1", **kw}
    return dr.parse_rule(raw, {"s1", "s2"})


class PeriodEndTests(unittest.TestCase):
    def test_days_exclude_first_day(self):
        """초일 불산입: 2A 견본 입국 2026-10-05 + 90일 → 2027-01-03."""
        base = date(2026, 10, 5)
        self.assertEqual(dr.period_end(base, 90, "day"), date(2027, 1, 3))
        for days, end in ((14, date(2026, 10, 19)), (30, date(2026, 11, 4)), (180, date(2027, 4, 3))):
            with self.subTest(days=days):
                self.assertEqual(dr.period_end(base, days, "day"), end)
        self.assertEqual(dr.period_end(base, 2, "week"), date(2026, 10, 19))

    def test_months_and_years_use_month_end_when_day_missing(self):
        cases = [
            (date(2026, 10, 5), 3, "month", date(2027, 1, 5)),
            (date(2027, 1, 31), 1, "month", date(2027, 2, 28)),
            (date(2028, 1, 31), 1, "month", date(2028, 2, 29)),   # 윤년
            (date(2026, 3, 31), 1, "month", date(2026, 4, 30)),
            (date(2026, 8, 31), 6, "month", date(2027, 2, 28)),
            (date(2026, 11, 30), 3, "month", date(2027, 2, 28)),
            (date(2028, 2, 29), 1, "year", date(2029, 2, 28)),
            (date(2026, 10, 5), 1, "year", date(2027, 10, 5)),
        ]
        for base, n, unit, end in cases:
            with self.subTest(base=base, n=n, unit=unit):
                self.assertEqual(dr.period_end(base, n, unit), end)


class ParseTests(unittest.TestCase):
    def test_date_formats(self):
        for text in ("2026년 10월 5일", "2026.10.05", "2026-10-05", "2026/10/5", "2026. 10. 5."):
            with self.subTest(text=text):
                self.assertEqual(dr.parse_date(text), date(2026, 10, 5))
        self.assertIsNone(dr.parse_date("2026년 13월 5일"))

    def test_base_date_on_label_line_only(self):
        self.assertEqual(dr.find_base_date(ORIGINAL, ["입국일"]), date(2026, 10, 5))
        self.assertEqual(dr.find_base_date("입국한 날: 2026.10.05", ["입국일", "입국한 날"]), date(2026, 10, 5))
        # 이름만 있는 줄 다음 줄의 날짜 (Railway 문서 #4처럼 OCR이 칸을 줄로 나눈 경우)
        self.assertEqual(dr.find_base_date("입국일\n2026년 10월 5일\n안내 내용", ["입국일"]), date(2026, 10, 5))
        self.assertEqual(dr.find_base_date("입국일 :\n 2026.10.05", ["입국일"]), date(2026, 10, 5))
        self.assertIsNone(dr.find_base_date("입국일 확인 필요\n2026년 10월 5일", ["입국일"]))   # 이름 뒤에 다른 말
        self.assertIsNone(dr.find_base_date("입국일\n안내\n2026년 10월 5일", ["입국일"]))      # 두 줄 뒤
        self.assertIsNone(dr.find_base_date("입국일\n발급 번호 12 2026년 10월 5일 발급", ["입국일"]))  # 줄 맨 앞 아님
        # "입국한 날부터 90일" 문장의 '입국한 날'은 뒤에 말이 이어져서 다음 줄을 보지 않음
        self.assertIsNone(dr.find_base_date("입국한 날부터 90일 이내\n2026년 11월 10일", ["입국한 날"]))
        self.assertIsNone(dr.find_base_date("발행일 2026년 11월 10일", ["입국일"]))          # 이름 없음
        self.assertIsNone(dr.find_base_date("입국일 2026.10.05\n입국일 2026.10.07", ["입국일"]))  # 서로 다른 날짜
        self.assertEqual(dr.find_base_date("입국일 2026.10.05\n(입국일 2026년 10월 5일)", ["입국일"]),
                         date(2026, 10, 5))                                                  # 같은 날짜면 OK
        self.assertIsNone(dr.find_base_date("생년월일: ******-*******", ["출생일", "생년월일"]))  # 마스킹

    def test_rule_shape_checked(self):
        self.assertIsNotNone(rule())
        for bad in ({"unit": "hour"}, {"amount": "많이"}, {"amount": 0}, {"sourceId": "s9"}, {"base": ""},
                    {"quote": ""}):
            with self.subTest(bad=bad):
                self.assertIsNone(rule(**bad))
        self.assertIsNone(dr.parse_rule("90일", {"s1"}))

    def test_base_date_only_right_after_label(self):
        """이름과 날짜 사이엔 공백·콜론만 (CodeRabbit). 다른 값·필드명이 끼면 그 기준일 날짜로 안 봄."""
        for text in ("입국일: 미상 / 발급일: 2026-10-05", "입국일 미상, 발급일 2026.10.05",
                     "입국일 / 2026-10-05", "입국일(예정) 2026-10-05"):
            with self.subTest(text=text):
                self.assertIsNone(dr.find_base_date(text, ["입국일"]))
        self.assertEqual(dr.find_base_date("입국일:2026-10-05", ["입국일"]), date(2026, 10, 5))
        self.assertEqual(dr.find_base_date("입국일 ：\n2026년 10월 5일", ["입국일"]), date(2026, 10, 5))
        self.assertIsNone(dr.find_base_date("입국일 -\n2026년 10월 5일", ["입국일"]))   # 이름 줄에 다른 기호

    def test_quote_base_must_be_rule_base(self):
        """인용문 '~부터' 앞 기준이 rule.base와 같아야 함 (CodeRabbit)."""
        self.assertTrue(dr.quote_base_matches(rule()))
        moved = "체류지를 변경한 날부터 14일 이내에"
        wrong = rule(amount=14, quote=moved)                       # base=입국일인데 체류지 변경 기준
        self.assertFalse(dr.quote_base_matches(wrong))
        self.assertFalse(dr.quote_supports_rule(wrong, moved))
        self.assertEqual(dr.judge(wrong, moved, ORIGINAL, DEADLINES).status, "UNVERIFIED")
        ok = rule(base="체류지 변경일", baseLabels=["체류지를 변경한 날"], amount=14, quote=moved)
        self.assertTrue(dr.quote_base_matches(ok))
        self.assertFalse(dr.quote_base_matches(rule(base="고지일", baseLabels=[], amount=1, unit="month",
                                                    quote="고지일로부터 1개월 이내에")))  # 지원하지 않는 기준
        self.assertFalse(dr.quote_base_matches(rule(quote="90일 이내에 신청")))  # '~부터' 기준이 없음

    def test_base_kind_decided_by_code_table(self):
        """기준일 종류는 인용문 표현 + 고정 표로 코드가 정함. LLM base·baseLabels로 바꿀 수 없음 (CodeRabbit)."""
        moved = "체류지를 변경한 날부터 14일 이내"
        # base=입국일인데 baseLabels에 체류지 변경일을 넣어 인용 검사를 통과시키려는 경우 → UNVERIFIED
        sneaky = rule(base="입국일", baseLabels=["체류지 변경일"], amount=14, quote=moved)
        self.assertIsNone(dr.base_kind(sneaky))
        original = "안내\n입국일 2026년 10월 5일\n신청 기한 2026년 10월 19일까지"
        j = dr.judge(sneaky, moved, original, [{"label": "신청 기한", "date": "2026-10-19"}])
        self.assertEqual(j.status, "UNVERIFIED")
        # 인용 기준(체류지 변경)과 같은 이름만 주면 계산하되, 날짜는 표의 이름으로만 찾음
        honest = rule(base="체류지 변경일", baseLabels=["체류지를 변경한 날"], amount=14, quote=moved)
        self.assertEqual(dr.base_kind(honest), "체류지 변경일")
        self.assertEqual(dr.judge(honest, moved, original, [{"label": "신청 기한", "date": "2026-10-19"}]).status,
                         "UNVERIFIED")  # 문서엔 입국일뿐 → 체류지 변경일 없음
        with_move = "안내\n체류지 변경일 2026.10.05\n신청 기한 2026년 10월 19일까지"
        self.assertEqual(dr.judge(honest, moved, with_move, [{"label": "신청 기한", "date": "2026-10-19"}]).status,
                         "MATCHED")
        # 표에 없는 기준 표현, 표에 없는 LLM 이름 → UNVERIFIED
        self.assertIsNone(dr.quote_base_kind("계약을 체결한 날부터 30일 이내"))
        self.assertIsNone(dr.base_kind(rule(baseLabels=["입국 예정일"])))
        self.assertIsNone(dr.base_kind(rule(baseLabels=["입국일자"])))       # 표의 이름만 (입국일, 입국한 날)
        self.assertIsNone(dr.base_kind(honest.__class__(**{**honest.__dict__, "labels": ["전입일"]})))
        # 기준 표현 전체가 표와 정확히 같아야 함: 앞에 다른 말이 붙으면 UNVERIFIED (CodeRabbit)
        for quote in ("재입국한 날부터 90일 이내", "최초 입국한 날부터 90일 이내", "다시 입국한 날부터 90일 이내",
                      "외국인이 다시 입국한 날부터 90일 이내", "재입국일부터 90일 이내", "입국하는 날부터 90일 이내"):
            with self.subTest(quote=quote):
                self.assertIsNone(dr.quote_base_kind(quote))
                self.assertEqual(dr.judge(rule(quote=quote), quote, ORIGINAL, DEADLINES).status, "UNVERIFIED")
        # 앞 절·주제와 끊기는 표현, 정부24 원문 표현("외국인이 입국한 날")은 인정
        gov24 = ("이 민원은 입국한 날로부터 90일을 초과하여 대한민국에 체류하게 될 "
                 "외국인이 입국한 날부터 90일 이내에 그의 체류지를 관할하는 출입국·외국인관서장에게 외국인등록을 하는 민원입니다.")
        for quote in ("이 민원은 입국한 날부터 90일 이내", "귀하는 입국한 날부터 90일 이내", gov24,
                      "체류하게 될 외국인이 입국한 날부터 90일 이내에", "입국한 날로부터 90일 이내"):
            with self.subTest(quote=quote):
                self.assertEqual(dr.quote_base_kind(quote), "입국일")
        self.assertEqual(dr.judge(rule(quote=gov24), gov24, ORIGINAL, DEADLINES).status, "MATCHED")
        # "로부터 90일을 초과하여"의 '부터'만 있고 '이내'가 없으면 기준 표현 없음
        self.assertIsNone(dr.quote_base_kind("입국한 날로부터 90일을 초과하여 체류"))
        # 지원 기준은 입국일·체류지 변경일 두 가지만 (표현도 정확히 일치)
        for quote, kind in (("입국한 날부터 90일 이내", "입국일"), ("입국일부터 90일 이내", "입국일"),
                            ("체류지를 변경한 날부터 14일 이내", "체류지 변경일"),
                            ("체류지 변경일부터 14일 이내", "체류지 변경일")):
            with self.subTest(quote=quote):
                self.assertEqual(dr.quote_base_kind(quote), kind)
        # 표에서 뺀 기준(고지 수령일/발송일처럼 같은 말이 여러 날일 수 있음)은 UNVERIFIED (CodeRabbit)
        for quote in ("고지일로부터 1개월 이내", "고지를 받은 날부터 30일 이내", "고지한 날부터 30일 이내",
                      "고지서를 받은 날부터 60일 이내", "전입한 날부터 14일 이내", "자격을 취득한 날부터 3개월 이내",
                      "출생한 날부터 90일 이내", "입국한 일부터 90일 이내"):
            with self.subTest(unsupported=quote):
                self.assertIsNone(dr.quote_base_kind(quote))
                j = dr.judge(rule(quote=quote), quote, ORIGINAL, DEADLINES)
                self.assertEqual(j.status, "UNVERIFIED")
                self.assertIn(f"공식 기준: {quote}", j.reason)

    def test_document_deadline_by_applies_to_without_fallback(self):
        """appliesTo 행을 먼저 고르고, 그 날짜를 못 읽으면 None. 다른 기한으로 대체 안 함 (CodeRabbit)."""
        rows = [{"label": "신청 기한", "date": "미정"}, {"label": "납부 기한", "date": "2027-01-03"}]
        self.assertIsNone(dr.pick_document_deadline(rows, "신청 기한"))
        self.assertEqual(self.judge_status(rows), "UNVERIFIED")
        self.assertEqual(dr.pick_document_deadline(rows, "납부 기한"), date(2027, 1, 3))
        self.assertIsNone(dr.pick_document_deadline([{"label": "신청 기한", "date": "미정"}], "신청 기한"))
        self.assertEqual(dr.pick_document_deadline([{"label": "기한", "date": "2027-01-03"}], "신청 기한"),
                         date(2027, 1, 3))                              # 기한이 하나뿐이면 그것
        self.assertIsNone(dr.pick_document_deadline(rows, ""))          # 여러 개인데 appliesTo 없음

    @staticmethod
    def judge_status(deadlines):
        return dr.judge(rule(), SOURCE, ORIGINAL, deadlines).status

    def test_quote_must_be_in_source_and_supported(self):
        self.assertTrue(dr.quote_supports_rule(rule(), SOURCE))
        self.assertFalse(dr.quote_supports_rule(rule(quote="입국한 날부터 30일 이내에"), SOURCE))  # 자료에 없음
        self.assertFalse(dr.quote_supports_rule(rule(amount=30), SOURCE))                          # 숫자 다름
        self.assertFalse(dr.quote_supports_rule(rule(unit="month"), SOURCE))                       # 단위 다름
        before = "체류기간 만료일 전까지 30일 이내에 신청"
        self.assertFalse(dr.quote_supports_rule(rule(quote=before, amount=30), before))           # 다른 모양


class JudgeTests(unittest.TestCase):
    def judge(self, r=None, original=ORIGINAL, deadlines=DEADLINES, source=SOURCE):
        return dr.judge(r or rule(), source, original, deadlines)

    def test_2a_sample_matches(self):
        j = self.judge()
        self.assertEqual((j.status, j.source_ids), ("MATCHED", ["s1"]))
        self.assertEqual(j.official_value, "입국일(2026-10-05)부터 90일 이내 → 2027-01-03")

    def test_changed_deadline_mismatch(self):
        j = self.judge(deadlines=[{"label": "신청 기한", "date": "2027-01-20"}])
        self.assertEqual(j.status, "MISMATCH")
        self.assertIn("2027-01-20", j.reason)
        self.assertEqual(j.official_value, "입국일(2026-10-05)부터 90일 이내 → 2027-01-03")

    def test_one_day_early_or_weekend_extension_is_unverified(self):
        # 2027-01-03은 일요일 → 01-04(월)은 휴일 연장일 수 있음, 01-02는 하루 빠름
        for d in ("2027-01-02", "2027-01-04"):
            with self.subTest(d=d):
                j = self.judge(deadlines=[{"label": "신청 기한", "date": d}])
                self.assertEqual((j.status, j.official_value, j.source_ids), ("UNVERIFIED", None, []))
                self.assertIn("공식 기준: 입국한 날부터 90일 이내에", j.reason)
        # 평일 만료일 다음 날은 연장 아님 → 불일치 (2026-10-19 월요일 + 1일)
        r = rule(amount=14, quote="입국한 날부터 14일 이내에")
        j = self.judge(r, deadlines=[{"label": "신청 기한", "date": "2026-10-20"}], source="입국한 날부터 14일 이내에")
        self.assertEqual(j.status, "MISMATCH")

    def test_missing_base_date_is_unverified_with_criterion(self):
        j = self.judge(original="외국인등록 안내\n신청 기한 2027년 1월 3일까지")
        self.assertEqual(j.status, "UNVERIFIED")
        self.assertIn("입국일을 찾지 못해", j.reason)
        self.assertEqual(dr._eul("고지일"), "고지일을")
        self.assertEqual(dr._eul("체류지 변경"), "체류지 변경을")
        self.assertEqual(dr._eul("출생 시기"), "출생 시기를")
        self.assertIn("공식 기준: 입국한 날부터 90일 이내에", j.reason)

    def test_supported_bases_and_units(self):
        """지원 기준(입국일·체류지 변경일)은 일·주·개월·년 모두 계산."""
        cases = [
            ("체류지 변경일", ["체류지 변경일"], 14, "day", "체류지를 변경한 날부터 14일 이내에",
             "체류지 변경일 2026.11.02", "2026-11-16"),
            ("체류지 변경일", ["체류지를 변경한 날"], 2, "week", "체류지를 변경한 날부터 2주 이내에",
             "체류지를 변경한 날: 2026-11-02", "2026-11-16"),
            ("입국일", ["입국일"], 1, "month", "입국한 날부터 1개월 이내에", "입국일 2027년 1월 31일", "2027-02-28"),
            ("입국일", ["입국한 날"], 1, "year", "입국일부터 1년 이내", "입국일 2028.02.29", "2029-02-28"),
        ]
        for base, labels, n, unit, quote, line, due in cases:
            with self.subTest(base=base, unit=unit):
                r = dr.parse_rule({"base": base, "baseLabels": labels, "amount": n, "unit": unit,
                                   "appliesTo": "신고 기한", "quote": quote, "sourceId": "s1"}, {"s1"})
                j = dr.judge(r, f"안내\n{quote} 신고해야 합니다.", f"안내문\n{line}\n신고 기한 {due}",
                             [{"label": "신고 기한", "date": due}])
                self.assertEqual(j.status, "MATCHED", j.reason)

    def test_unsupported_bases_are_unverified(self):
        """고지일·출생일·자격 취득일 등은 지원하지 않음: 문서에 날짜가 있어도 UNVERIFIED, reason에 공식 기준."""
        cases = [
            ("고지일", ["고지일"], "고지일로부터 1개월 이내에", "고지일 2027년 1월 31일", "2027-02-28"),
            ("고지일", ["고지를 받은 날"], "고지를 받은 날부터 30일 이내", "고지를 받은 날 2026.10.05", "2026-11-04"),
            ("고지일", ["고지한 날"], "고지한 날부터 30일 이내", "고지한 날 2026.10.05", "2026-11-04"),
            ("자격 취득일", ["자격 취득일"], "자격 취득일부터 3개월 이내", "자격 취득일: 2026-08-31", "2026-11-30"),
            ("출생일", ["출생일"], "출생한 날부터 1년 이내", "출생일 2028.02.29", "2029-02-28"),
            ("출생일", ["생년월일"], "출생한 날부터 90일 이내", "생년월일: ******-*******", "2027-01-03"),
        ]
        for base, labels, quote, line, due in cases:
            with self.subTest(quote=quote):
                r = dr.parse_rule({"base": base, "baseLabels": labels, "amount": 1, "unit": "day",
                                   "appliesTo": "신고 기한", "quote": quote, "sourceId": "s1"}, {"s1"})
                j = dr.judge(r, f"안내\n{quote} 신고해야 합니다.", f"안내문\n{line}\n신고 기한 {due}",
                             [{"label": "신고 기한", "date": due}])
                self.assertEqual((j.status, j.official_value), ("UNVERIFIED", None))
                self.assertIn(f"공식 기준: {quote}", j.reason)

    def test_deadline_choice_with_several_deadlines(self):
        two = [{"label": "신청 기한", "date": "2027-01-03"}, {"label": "납부 기한", "date": "2026-12-31"}]
        self.assertEqual(self.judge(deadlines=two).status, "MATCHED")                       # appliesTo로 고름
        self.assertEqual(self.judge(rule(appliesTo="모르는 기한"), deadlines=two).status, "UNVERIFIED")


class PromptTests(unittest.TestCase):
    def test_verify_prompt_asks_rule_not_calculation(self):
        from ai.chatbot import prompts

        for phrase in ('"rule"', "baseLabels", "appliesTo", "날짜를 직접 계산하지 말고", "글자 그대로",
                       '"day"|"week"|"month"|"year"'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prompts.VERIFY_SYSTEM)


if __name__ == "__main__":
    unittest.main()
