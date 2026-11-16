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

    def test_other_bases_and_units(self):
        cases = [
            ("체류지 변경일", ["체류지 변경일", "전입일"], 14, "day", "체류지를 변경한 날부터 14일 이내에",
             "전입일 2026.11.02", "2026-11-16"),
            ("고지일", ["고지일", "고지한 날"], 1, "month", "고지일로부터 1개월 이내에",
             "고지일 2027년 1월 31일", "2027-02-28"),
            ("자격 취득일", ["자격 취득일", "자격취득일"], 3, "month", "자격 취득일부터 3개월 이내",
             "자격취득일: 2026-08-31", "2026-11-30"),
            ("출생일", ["출생일", "출생한 날"], 1, "year", "출생한 날부터 1년 이내",
             "출생일 2028.02.29", "2029-02-28"),
        ]
        for base, labels, n, unit, quote, line, due in cases:
            with self.subTest(base=base):
                r = dr.parse_rule({"base": base, "baseLabels": labels, "amount": n, "unit": unit,
                                   "appliesTo": "신고 기한", "quote": quote, "sourceId": "s1"}, {"s1"})
                j = dr.judge(r, f"안내\n{quote} 신고해야 합니다.", f"안내문\n{line}\n신고 기한 {due}",
                             [{"label": "신고 기한", "date": due}])
                self.assertEqual(j.status, "MATCHED", j.reason)

    def test_masked_birth_date_is_unverified(self):
        r = dr.parse_rule({"base": "출생일", "baseLabels": ["출생일", "생년월일"], "amount": 90, "unit": "day",
                           "appliesTo": "신청 기한", "quote": "출생한 날부터 90일 이내", "sourceId": "s1"}, {"s1"})
        j = dr.judge(r, "출생한 날부터 90일 이내 신청", "생년월일: ******-*******\n신청 기한 2027-01-03", DEADLINES)
        self.assertEqual(j.status, "UNVERIFIED")

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
