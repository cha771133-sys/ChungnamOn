"""상대 기한 계산·판정 (verify DEADLINE, deadline_rules)."""
import unittest
from datetime import date

from ai.chatbot import deadline_rules as dr

# 공식 원문 구절 기준으로 검사하므로, 기준 표현이 구절 시작에 오는 문장 ("이 민원은 입국한 날부터"는 UNVERIFIED)
SOURCE = "외국인등록 안내입니다.\n입국한 날부터 90일 이내에 체류지를 관할하는 출입국·외국인관서장에게 신청해야 합니다."
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

    def test_label_must_start_at_word_boundary(self):
        """'재입국일'의 '입국일'처럼 이름 바로 앞이 한글이면 그 이름이 아님 (CodeRabbit)."""
        self.assertIsNone(dr.find_base_date("재입국일 2026-10-05", ["입국일", "입국한 날"]))
        self.assertIsNone(dr.find_base_date("재입국한 날: 2026.10.05", ["입국일", "입국한 날"]))
        self.assertIsNone(dr.find_base_date("재입국일\n2026년 10월 5일", ["입국일"]))
        # 둘 다 있으면 '입국일' 날짜만
        both = "재입국일 2026-12-01\n입국일 2026-10-05"
        self.assertEqual(dr.find_base_date(both, ["입국일", "입국한 날"]), date(2026, 10, 5))
        self.assertEqual(dr.find_base_date("(입국일) 2026-10-05", ["입국일"]), None)  # 괄호 뒤 다른 글자
        self.assertEqual(dr.find_base_date("·입국일 2026-10-05", ["입국일"]), date(2026, 10, 5))
        # 판정: 재입국일만 있으면 UNVERIFIED, 둘 다 있으면 입국일로 MATCHED
        self.assertEqual(dr.judge(rule(), SOURCE, "재입국일 2026-10-05\n신청 기한 2027-01-03", DEADLINES).status,
                         "UNVERIFIED")
        self.assertEqual(dr.judge(rule(), SOURCE, both, DEADLINES).status, "MATCHED")

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
        # 정부24 원문 표현("외국인이 입국한 날")처럼 구절 전체가 표와 같으면 인정 (인용문 시작·문장부호 뒤)
        gov24 = ("이 민원은 입국한 날로부터 90일을 초과하여 대한민국에 체류하게 될 "
                 "외국인이 입국한 날부터 90일 이내에 그의 체류지를 관할하는 출입국·외국인관서장에게 외국인등록을 하는 민원입니다.")
        for quote in ("외국인이 입국한 날부터 90일 이내에", "입국한 날로부터 90일 이내", "안내: 입국한 날부터 90일 이내",
                      "(입국일부터 90일 이내)"):
            with self.subTest(quote=quote):
                self.assertEqual(dr.quote_base_kind(quote), "입국일")
        quote_2a = "외국인이 입국한 날부터 90일 이내에"
        self.assertEqual(dr.judge(rule(quote=quote_2a), gov24, ORIGINAL, DEADLINES).status, "MATCHED")
        # 긴 표현("외국인이 입국한 날")은 앞에 다른 말이 있어도 낱말 시작이면 인정 → 정부24 문장 전체도 입국일
        for quote in (gov24, "체류하게 될 외국인이 입국한 날부터 90일 이내에"):
            with self.subTest(long=quote):
                self.assertEqual(dr.quote_base_kind(quote), "입국일")
        self.assertEqual(dr.judge(rule(quote=gov24), gov24, ORIGINAL, DEADLINES).status, "MATCHED")
        # 짧은 표현 앞에 한글 낱말이 붙거나, 금지어가 있거나, 긴 표현이 한글에 붙으면 UNVERIFIED
        for quote in ("다시 한국에 입국한 날부터 90일 이내", "이 민원은 입국한 날부터 90일 이내",
                      "귀하는 입국한 날부터 90일 이내", "재차 입국한 날부터 90일 이내", "새로 입국한 날부터 90일 이내",
                      "한국에, 다시 입국한 날부터 90일 이내", "다시 한국에 외국인이 입국한 날부터 90일 이내",
                      "재외국인이 입국한 날부터 90일 이내", "재입국한 외국인이 입국한 날부터 90일 이내"):
            with self.subTest(blocked=quote):
                self.assertIsNone(dr.quote_base_kind(quote))
        reentry = "다시 한국에 입국한 날부터 90일 이내"
        self.assertEqual(dr.judge(rule(quote=reentry), reentry, ORIGINAL, DEADLINES).status, "UNVERIFIED")
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

    def test_quote_checked_in_official_source_context(self):
        """인용문만 보지 않고 공식 원문에서 그 위치의 앞 문맥까지 붙인 '원문 구절'로 검사 (CodeRabbit)."""
        short = "입국한 날부터 90일 이내"
        gov24 = ("이 민원은 입국한 날로부터 90일을 초과하여 대한민국에 체류하게 될 외국인이 입국한 날부터 "
                 "90일 이내에 그의 체류지를 관할하는 출입국·외국인관서장에게 외국인등록을 하는 민원입니다.")
        cases = [
            ("원문 재입국 + 짧게 잘라 인용", "재입국한 날부터 90일 이내에 신고해야 합니다.", short, "UNVERIFIED"),
            ("원문 다시 한국에 + 잘라 인용", "다시 한국에 입국한 날부터 90일 이내에 신고해야 합니다.", short, "UNVERIFIED"),
            ("원문 정부24 + 짧게 인용", gov24, short, "MATCHED"),
            ("원문 정부24 + 문장 전체 인용", gov24, gov24, "MATCHED"),
            ("원문 이 민원은 + 잘라 인용", "이 민원은 입국한 날부터 90일 이내에 신청합니다.", short, "UNVERIFIED"),
            # 여러 번 나오면 모든 위치가 통과해야 함
            ("두 번, 둘 다 통과", "입국한 날부터 90일 이내에 신청.\n입국한 날부터 90일 이내에 방문.", short, "MATCHED"),
            ("두 번, 하나는 재입국", "입국한 날부터 90일 이내에 신청.\n재입국한 날부터 90일 이내에 신고.", short,
             "UNVERIFIED"),
            ("두 번, 하나는 다시", "입국한 날부터 90일 이내에 신청. 다시 한국에 입국한 날부터 90일 이내에 신고.", short,
             "UNVERIFIED"),
        ]
        for name, source, quote, expected in cases:
            with self.subTest(name):
                j = dr.judge(rule(quote=quote), source, ORIGINAL, DEADLINES)
                self.assertEqual(j.status, expected, j.reason)
        self.assertIsNone(dr.source_passages(short, "재입국한 날부터 90일 이내"))
        self.assertIsNone(dr.source_passages(short, "출국한 날부터 30일 이내"))      # 원문에 없음
        self.assertEqual(dr.source_passages(short, "안내입니다.\n귀하는, 입국한 날부터 90일 이내"), [" 귀하는, 입국한 날부터 90일 이내"])

    def test_reentry_word_before_punctuation_in_same_sentence(self):
        """쉼표·괄호 앞 수식어도 같은 문장이면 금지어 검사 (CodeRabbit): '재입국 시, 외국인이 입국한 날부터' → UNVERIFIED."""
        reentry = "재입국 시, 외국인이 입국한 날부터 90일 이내"
        self.assertIsNone(dr.quote_base_kind(reentry))
        self.assertEqual(dr.judge(rule(quote=reentry), reentry, ORIGINAL, DEADLINES).status, "UNVERIFIED")
        # LLM이 쉼표 뒤만 잘라 인용해도 원문 문장 기준으로 막힘
        for quote in ("외국인이 입국한 날부터 90일 이내", "입국한 날부터 90일 이내"):
            with self.subTest(quote=quote):
                self.assertEqual(dr.judge(rule(quote=quote), reentry + "에 신고합니다.", ORIGINAL, DEADLINES).status,
                                 "UNVERIFIED")
        for source in ("(재입국의 경우) 입국한 날부터 90일 이내", "다시 오는 경우, 입국한 날부터 90일 이내"):
            with self.subTest(source=source):
                self.assertEqual(dr.judge(rule(quote="입국한 날부터 90일 이내"), source, ORIGINAL, DEADLINES).status,
                                 "UNVERIFIED")
        # 금지어가 앞 문장에 있으면 다른 문장이라 영향 없음
        other_sentence = "재입국은 별도 절차입니다. 외국인이 입국한 날부터 90일 이내에 신청합니다."
        self.assertEqual(dr.judge(rule(quote="외국인이 입국한 날부터 90일 이내에"), other_sentence, ORIGINAL,
                                  DEADLINES).status, "MATCHED")

    def test_base_and_period_only_from_quoted_rule(self):
        """한 문장에 규칙이 둘이면, 기준·기간은 인용한 규칙에서만 (CodeRabbit). 앞 규칙의 기준을 쓰지 않음."""
        source = "입국한 날부터 90일 이내에 등록하고, 체류지를 변경한 날부터 14일 이내에 신고합니다."
        second = "체류지를 변경한 날부터 14일 이내에"
        first = "입국한 날부터 90일 이내에"
        doc = "안내문\n입국일 2026-10-05\n체류지 변경일 2026-11-02\n신고 기한 2026-10-19"
        # 두 번째 규칙 인용 + base=입국일 + 기한=입국일+14일 → UNVERIFIED (기준이 다름)
        wrong = dr.parse_rule({"base": "입국일", "baseLabels": ["입국일"], "amount": 14, "unit": "day",
                               "appliesTo": "신고 기한", "quote": second, "sourceId": "s1"}, {"s1"})
        self.assertEqual(dr.judge(wrong, source, doc, [{"label": "신고 기한", "date": "2026-10-19"}]).status,
                         "UNVERIFIED")
        # 두 번째 규칙 인용 + base=체류지 변경일 + 기한=체류지 변경일+14일 → MATCHED
        right = dr.parse_rule({"base": "체류지 변경일", "baseLabels": ["체류지 변경일"], "amount": 14, "unit": "day",
                               "appliesTo": "신고 기한", "quote": second, "sourceId": "s1"}, {"s1"})
        j = dr.judge(right, source, doc, [{"label": "신고 기한", "date": "2026-11-16"}])
        self.assertEqual((j.status, j.official_value),
                         ("MATCHED", "체류지 변경일(2026-11-02)부터 14일 이내 → 2026-11-16"))
        # 첫 번째 규칙 인용 + base=입국일 + 입국일+90일 → MATCHED
        j = dr.judge(rule(quote=first), source, doc, [{"label": "신청 기한", "date": "2027-01-03"}])
        self.assertEqual(j.status, "MATCHED")
        # 쉼표 없이 '하고'로 이어져도, 줄이 나뉘어도 같음
        for src in ("입국한 날부터 90일 이내에 등록하고 체류지를 변경한 날부터 14일 이내에 신고합니다.",
                    "입국한 날부터 90일 이내에 등록하고\n체류지를 변경한 날부터 14일 이내에 신고합니다.",
                    "입국한 날부터 90일 이내에 체류지를 변경한 날부터 14일 이내에 신고"):
            with self.subTest(src=src):
                self.assertEqual(dr.judge(wrong, src, doc, [{"label": "신고 기한", "date": "2026-10-19"}]).status,
                                 "UNVERIFIED")
                self.assertEqual(dr.judge(right, src, doc, [{"label": "신고 기한", "date": "2026-11-16"}]).status,
                                 "MATCHED")
        # 인용 안에 규칙이 둘이면 하나로 정할 수 없음 → 기준 없음
        self.assertIsNone(dr.quote_base_kind(source))
        # 기준 표현이 인용 시작 바로 앞에 공백으로만 붙어 있으면 포함 (정부24 '외국인이 입국한 날')
        text = "체류하게 될 외국인이 입국한 날부터 90일 이내에 신청"
        start = text.index("입국한")
        self.assertEqual(dr.rule_kind_at(text, start, text.index("신청")), "입국일")

    def test_words_after_quote_in_same_sentence(self):
        """인용 범위 밖이어도 규칙이 든 문장 끝까지(괄호 안 포함) 금지어·단서를 검사 (CodeRabbit). 다음 문장은 제외."""
        short = "입국한 날부터 90일 이내"
        blocked = [
            "입국한 날부터 90일 이내에 신고해야 합니다(재입국한 외국인에 한함).",
            "입국한 날부터 90일 이내에 신고해야 합니다(재입국자의 경우).",
            "입국한 날부터 90일 이내에 신고하되 다시 입국하면 새로 신고합니다.",
            # 같은 종류의 구멍: 기간 계산 방식이 바뀌거나 뜻이 뒤집히는 단서
            "입국한 날부터 90일 이내(공휴일 제외)에 신고합니다.",
            "입국한 날부터 90일 이내(영업일 기준)에 신고합니다.",
            "입국한 날부터 90일 이내에는 신고할 수 없습니다.",
            "입국한 날부터 90일 이내에 신고하며, 기간 연장이 가능합니다.",
            "입국한 날부터 90일 이내에 신고하고, 다만 예외가 있습니다.",
        ]
        for source in blocked:
            with self.subTest(blocked=source):
                self.assertEqual(dr.judge(rule(quote=short), source, ORIGINAL, DEADLINES).status, "UNVERIFIED")
        # 금지어·단서가 다음 문장에만 있으면 영향 없음
        for source in ("입국한 날부터 90일 이내에 신고합니다. 재입국한 외국인은 별도로 안내합니다.",
                       "입국한 날부터 90일 이내에 신고합니다! 공휴일에는 방문할 수 없습니다.",
                       "입국한 날부터 90일 이내에 신고합니다.\n(재입국자는 다음 안내를 따릅니다.)"):
            with self.subTest(next_sentence=source):
                self.assertEqual(dr.judge(rule(quote=short), source, ORIGINAL, DEADLINES).status, "MATCHED")
        # 정부24 원문(줄 나뉨 포함)은 계속 MATCHED
        gov24 = ("이 민원은 입국한 날로부터 90일을 초과하여 대한민국에 체류하게 될 외국인이\n입국한 날부터 "
                 "90일 이내에 그의 체류지를 관할하는\n출입국·외국인관서장에게 외국인등록을 하는 민원입니다.")
        self.assertEqual(dr.judge(rule(quote=short), gov24, ORIGINAL, DEADLINES).status, "MATCHED")

    def test_source_context_not_cut_at_newline(self):
        """공식 원문 줄바꿈에서 문맥을 자르지 않음 (CodeRabbit #115). 줄바꿈·연속 공백은 공백 하나."""
        short = "입국한 날부터 90일 이내"
        for source in ("다시 한국에\n입국한 날부터 90일 이내", "재\n입국한 날부터 90일 이내", "재입국 시\n\n입국한 날부터 90일 이내",
                       "새로\r\n  입국한 날부터 90일 이내"):
            with self.subTest(source=source):
                self.assertEqual(dr.judge(rule(quote=short), source, ORIGINAL, DEADLINES).status, "UNVERIFIED")
        self.assertEqual(dr.source_passages(short, "다시 한국에\n입국한 날부터 90일 이내"),
                         ["다시 한국에 입국한 날부터 90일 이내"])
        # 정부24 문장이 줄로 나뉘어도 긴 표현 '외국인이 입국한 날'로 MATCHED (짧게 인용해도, 나뉜 그대로 인용해도)
        gov24 = ("이 민원은 입국한 날로부터 90일을 초과하여 대한민국에 체류하게 될 외국인이\n입국한 날부터 "
                 "90일 이내에 그의 체류지를 관할하는\n출입국·외국인관서장에게 외국인등록을 하는 민원입니다.")
        for quote in (short, "외국인이 입국한 날부터 90일 이내에", "외국인이\n입국한 날부터 90일 이내에"):
            with self.subTest(quote=quote):
                self.assertEqual(dr.judge(rule(quote=quote), gov24, ORIGINAL, DEADLINES).status, "MATCHED")
        # 앞 문장이 마침표로 끝나면 줄바꿈 뒤는 새 문장 → 짧은 표현도 인정
        self.assertEqual(dr.judge(rule(quote=short), "안내입니다.\n입국한 날부터 90일 이내에 신청", ORIGINAL,
                                  DEADLINES).status, "MATCHED")

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
                j = dr.judge(r, f"안내입니다.\n{quote} 신고해야 합니다.", f"안내문\n{line}\n신고 기한 {due}",
                             [{"label": "신고 기한", "date": due}])
                self.assertEqual(j.status, "MATCHED", j.reason)

    def test_unsupported_bases_are_unverified(self):
        """고지일·출생일·자격 취득일 등은 지원하지 않음: 문서에 날짜가 있어도 UNVERIFIED, reason에 공식 기준."""
        # 인용문마다 맞는 기간·단위를 줘서, 기간 불일치가 아니라 '지원하지 않는 기준' 때문에 UNVERIFIED인지 확인
        cases = [
            ("고지일", ["고지일"], "고지일로부터 1개월 이내에", 1, "month", "고지일 2027년 1월 31일", "2027-02-28"),
            ("고지일", ["고지를 받은 날"], "고지를 받은 날부터 30일 이내", 30, "day",
             "고지를 받은 날 2026.10.05", "2026-11-04"),
            ("고지일", ["고지한 날"], "고지한 날부터 30일 이내", 30, "day", "고지한 날 2026.10.05", "2026-11-04"),
            ("자격 취득일", ["자격 취득일"], "자격 취득일부터 3개월 이내", 3, "month",
             "자격 취득일: 2026-08-31", "2026-11-30"),
            ("출생일", ["출생일"], "출생한 날부터 1년 이내", 1, "year", "출생일 2028.02.29", "2029-02-28"),
            ("출생일", ["생년월일"], "출생한 날부터 90일 이내", 90, "day", "생년월일: ******-*******", "2027-01-03"),
        ]
        for base, labels, quote, amount, unit, line, due in cases:
            with self.subTest(quote=quote):
                r = dr.parse_rule({"base": base, "baseLabels": labels, "amount": amount, "unit": unit,
                                   "appliesTo": "신고 기한", "quote": quote, "sourceId": "s1"}, {"s1"})
                # 기간·단위는 인용문과 같음 → 막히는 이유는 기준일 종류뿐
                self.assertEqual([(int(n), dr.UNIT_WORDS[u]) for n, u in dr.PERIOD_RE.findall(quote)],
                                 [(amount, unit)])
                self.assertIsNone(dr.quote_base_kind(quote))
                j = dr.judge(r, f"안내입니다.\n{quote} 신고해야 합니다.", f"안내문\n{line}\n신고 기한 {due}",
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
                       "기준 표현부터 'N일 이내'까지만 짧게", "'외국인이 입국한 날부터 90일 이내에'",
                       '"day"|"week"|"month"|"year"'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prompts.VERIFY_SYSTEM)


if __name__ == "__main__":
    unittest.main()
