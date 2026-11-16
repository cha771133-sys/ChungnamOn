"""상대 기한 확인 (verify DEADLINE): 공식 기준 "기준일부터 N일/주/개월/년 이내"를 코드가 계산한다.

LLM은 공식 자료에서 규칙을 구조로만 뽑고 (rule: base·baseLabels·amount·unit·appliesTo·quote), 날짜 계산은 하지 않는다.
이 모듈이 문서 원문에서 기준일을 찾아 만료일을 계산하고 문서 기한과 비교한다.

계산 (민법 기간 계산, 초일 불산입):
- 일·주 (157조): 기준일 다음 날부터 센다 → 만료일 = 기준일 + N일 (주는 7일)
  예) 입국일 2026-10-05 + 90일 → 2027-01-03 (2A 견본과 같음)
- 개월·년 (160조): 기준일과 같은 날짜의 N개월(년) 뒤. 그 달에 그 날짜가 없으면 그 달 말일
  예) 2027-01-31 + 1개월 → 2027-02-28, 2028-02-29 + 1년 → 2029-02-28
- 말일이 휴일이면 다음 날로 늦추는 규칙(161조)은 공휴일 달력이 없어서 계산에 넣지 않는다.
  대신 문서 기한이 휴일 연장으로 볼 수 있는 날이면 확정하지 않는다 (UNVERIFIED).

판정 (문서 기한 D, 계산한 만료일 E):
- D == E → MATCHED
- D == E − 1 (기준일 포함 여부에 따른 하루 차이) → UNVERIFIED
- E < D이고 E~D−1이 모두 토·일 (휴일 연장일 수 있음) → UNVERIFIED
- 그 밖 → MISMATCH
기준일을 못 찾거나, 규칙이 지원하는 모양이 아니거나, 자료 본문에 인용이 없으면 UNVERIFIED.

로그에는 원문·날짜를 남기지 않는다 (이 모듈은 로그를 쓰지 않음).
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

UNITS = {"day": "일", "week": "주", "month": "개월", "year": "년"}
# 인용문에서 기간 표현: "90일", "3개월", "1년", "2주" (+ "이내")
UNIT_WORDS = {"일": "day", "주": "week", "개월": "month", "달": "month", "년": "year"}
PERIOD_RE = re.compile(r"(\d{1,4})\s*(개월|일|주|달|년)\s*(?:이내|안에|내에|까지)")
# 지원하지 않는 모양 (기준일 '전', 만료 전, 경과 후 등)
UNSUPPORTED_RE = re.compile(r"전까지|전에|이전|경과\s*후|지난\s*후|이후|후에|만료")
# 문서 원문 날짜: 2026년 10월 5일 / 2026.10.05 / 2026-10-05 / 2026/10/5 / 2026. 10. 5.
DATE_RE = re.compile(
    r"(?<!\d)(\d{4})\s*(?:년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일|[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2}))(?!\d)"
)
MAX_LABEL_GAP = 20  # 기준일 이름 뒤 몇 글자 안의 날짜만 그 기준일로 본다


@dataclass
class Rule:
    base: str               # 기준 이름 (입국일, 체류지 변경일, 출생일, 고지일, 자격 취득일 …)
    labels: list[str]       # 문서에서 찾을 이름 (입국일, 입국한 날 …)
    amount: int
    unit: str               # day | week | month | year
    applies_to: str         # 어떤 문서 기한에 대한 규칙인지 (label)
    quote: str              # 공식 자료 문장 그대로
    source_id: str          # s1 …

    def describe(self) -> str:
        return f"{self.base}부터 {self.amount}{UNITS[self.unit]} 이내"


@dataclass
class Judgement:
    status: str             # MATCHED | MISMATCH | UNVERIFIED
    official_value: str | None
    reason: str
    source_ids: list[str]


def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _eul(word: str) -> str:
    """목적격 조사: 받침 있으면 '을', 없으면 '를' (한글이 아니면 '을(를)')."""
    last = word[-1:] if word else ""
    if not ("가" <= last <= "힣"):
        return f"{word}을(를)"
    return f"{word}{'을' if (ord(last) - 0xAC00) % 28 else '를'}"


def parse_rule(raw, valid_ids: set[str]) -> Rule | None:
    """LLM이 낸 rule을 검사해서 Rule로. 모양이 이상하면 None."""
    if not isinstance(raw, dict):
        return None
    try:
        amount = int(raw.get("amount"))
    except (TypeError, ValueError):
        return None
    unit = _text(raw.get("unit")).lower()
    base = _text(raw.get("base"))
    quote = _text(raw.get("quote"))
    labels = raw.get("baseLabels", raw.get("base_labels"))
    labels = [_text(x) for x in labels if _text(x)] if isinstance(labels, list) else []
    source_id = _text(raw.get("sourceId", raw.get("source_id"))).lower()
    if unit not in UNITS or not (0 < amount <= 3650) or not base or not quote or source_id not in valid_ids:
        return None
    return Rule(base, list(dict.fromkeys([base, *labels])), amount, unit,
                _text(raw.get("appliesTo", raw.get("applies_to"))), quote, source_id)


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def quote_supports_rule(rule: Rule, source_text: str) -> bool:
    """인용문이 자료 본문에 실제로 있고, '기준부터 N단위 이내' 모양이며, 숫자·단위가 rule과 같은지."""
    if not rule.quote or _squeeze(rule.quote) not in _squeeze(source_text):
        return False
    if UNSUPPORTED_RE.search(rule.quote):
        return False
    periods = [(int(n), UNIT_WORDS[u]) for n, u in PERIOD_RE.findall(rule.quote)]
    return periods == [(rule.amount, rule.unit)]  # 기간 표현이 정확히 하나이고 rule과 같아야 함


def _date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text or "")
    return _date(m[1], m[2] or m[4], m[3] or m[5]) if m else None


def _date_near_start(text: str, gap: int = MAX_LABEL_GAP) -> date | None:
    """text 앞쪽 gap자 안에서 시작하는 날짜."""
    d = DATE_RE.search(text[:gap + 20])
    return _date(d[1], d[2] or d[4], d[3] or d[5]) if d and d.start() <= gap else None


def find_base_date(original_text: str, labels: list[str]) -> date | None:
    """원문에서 기준일 이름 바로 뒤(MAX_LABEL_GAP자 안)의 날짜. 서로 다른 날짜가 여럿이면 None.

    - 같은 줄: "입국일 2026년 10월 5일", "입국한 날: 2026.10.05"
    - 다음 줄: 이름만 있는 줄 다음 줄 맨 앞의 날짜 (OCR이 표의 칸을 줄로 나눈 경우: "입국일\\n2026년 10월 5일")
    """
    lines = (original_text or "").splitlines()
    found = set()
    for n, line in enumerate(lines):
        for label in labels:
            for m in re.finditer(re.escape(label), line):
                rest = line[m.end():]
                value = _date_near_start(rest)
                if value is None and not re.sub(r"[\s:：\-–—·.()\[\]]", "", rest) and n + 1 < len(lines):
                    value = _date_near_start(lines[n + 1].strip(), gap=0)  # 이름만 있는 줄 → 다음 줄 맨 앞 날짜
                if value:
                    found.add(value)
    return found.pop() if len(found) == 1 else None


def add_months(base: date, months: int) -> date:
    """같은 날짜의 N개월 뒤. 그 달에 그 날짜가 없으면 말일 (민법 160조 3항)."""
    y, m = divmod(base.month - 1 + months, 12)
    year, month = base.year + y, m + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def period_end(base: date, amount: int, unit: str) -> date:
    """초일 불산입 만료일."""
    if unit == "day":
        return base + timedelta(days=amount)
    if unit == "week":
        return base + timedelta(weeks=amount)
    if unit == "month":
        return add_months(base, amount)
    if unit == "year":
        return add_months(base, 12 * amount)
    raise ValueError(unit)


def _maybe_holiday_extension(end: date, doc_date: date) -> bool:
    """만료일 다음부터 문서 기한 전날까지가 모두 토·일이면 휴일 연장(161조)일 수 있음."""
    if doc_date <= end:
        return False
    days = (doc_date - end).days
    return days <= 3 and all((end + timedelta(days=i)).weekday() >= 5 for i in range(days))


def pick_document_deadline(deadlines, applies_to: str) -> date | None:
    """비교할 문서 기한. 하나뿐이면 그것, 여러 개면 label이 applies_to와 같은 것 하나."""
    rows = [(_text(d.get("label")), parse_date(_text(d.get("date"))) or _iso(d.get("date")))
            for d in deadlines or [] if isinstance(d, dict)]
    rows = [(label, value) for label, value in rows if value]
    if len(rows) == 1:
        return rows[0][1]
    key = _squeeze(applies_to)
    matched = {value for label, value in rows if key and _squeeze(label) == key}
    return matched.pop() if len(matched) == 1 else None


def _iso(value) -> date | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", _text(value))
    return _date(*m.groups()) if m else None


def judge(rule: Rule, source_text: str, original_text: str, deadlines) -> Judgement:
    """상대 기한 판정. 공식 기준은 reason에 항상 적는다."""
    criterion = f"공식 기준: {rule.quote}"

    def unverified(why: str) -> Judgement:
        return Judgement("UNVERIFIED", None, f"{why} ({criterion})", [])

    if not quote_supports_rule(rule, source_text):
        return unverified("공식 자료의 기한 기준을 계산할 수 있는 모양으로 확인하지 못했어요")
    doc_date = pick_document_deadline(deadlines, rule.applies_to)
    if doc_date is None:
        return unverified("공식 기준과 비교할 문서 기한을 하나로 정하지 못했어요")
    base = find_base_date(original_text, rule.labels)
    if base is None:
        return unverified(f"문서에서 {_eul(rule.base)} 찾지 못해 기한을 계산할 수 없어요")

    end = period_end(base, rule.amount, rule.unit)
    official = f"{rule.base}({base.isoformat()})부터 {rule.amount}{UNITS[rule.unit]} 이내 → {end.isoformat()}"
    if doc_date == end:
        return Judgement("MATCHED", official, f"{official}로 문서 기한과 같아요.", [rule.source_id])
    if doc_date == end - timedelta(days=1):
        return unverified(f"{official}인데 문서 기한은 하루 빨라요. {rule.base} 당일을 포함해 세면 같을 수 있어요")
    if _maybe_holiday_extension(end, doc_date):
        return unverified(f"{official}인데 그날이 주말이라 기한이 늦춰졌을 수 있어요")
    return Judgement("MISMATCH", official, f"{official}인데 문서 기한은 {doc_date.isoformat()}이에요.",
                     [rule.source_id])
