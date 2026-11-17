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
# 기준일 이름과 날짜 사이에 허용하는 것: 공백·콜론만 (다른 필드명·"/"·"미상" 등이 끼면 그 기준일 날짜로 안 봄)
LABEL_SEP_RE = re.compile(r"[ \t:：]*")
# 인용문의 기준 표현: "입국한 날부터 90일 이내", "고지일로부터 1개월 이내" → "부터" 앞부분
# ('이내' 등이 붙은 기간 앞의 '부터'만. "입국한 날로부터 90일을 초과하여"의 '부터'는 아님)
BASE_PHRASE_RE = re.compile(
    r"(.*?)(?:으로부터|로부터|부터)\s*\d{1,4}\s*(?:개월|일|주|달|년)\s*(?:이내|안에|내에|까지)", re.S
)
# 기준 표현이 속한 구절은 인용문 시작이나 문장부호에서 시작한다. 구절 전체가 표의 표현과 정확히 같아야 한다.
# 앞에 한글 낱말이 붙으면(이 민원은·다시 한국에·체류하게 될 …) 경계로 보지 않는다 (애매하면 UNVERIFIED).
PHRASE_BREAK_RE = re.compile(r"[,.;:()「」『』\"'“”‘’·\[\]<>]")
# 주어가 들어 있는 긴 표현: 앞에 다른 말이 있어도 '낱말 시작'(구절 시작·공백 뒤)이면 인정.
# 짧은 표현(입국한 날·입국일·체류지를 변경한 날 …)은 구절 전체가 정확히 같아야 한다.
# 예) "…체류하게 될 외국인이 입국한 날부터 90일 이내에"(정부24 원문 문장 전체) → 입국일
LONG_PHRASES = {"입국일": [("외국인이", "입국한", "날")]}
LONG_PHRASE_RES = {
    kind: [re.compile(r"(?:^|(?<=\s))" + r"\s*".join(map(re.escape, words)) + r"\s*$") for words in phrases]
    for kind, phrases in LONG_PHRASES.items()
}
# 구절에 이 말이 있으면 다른 입국(재입국 등)일 수 있어 규칙을 쓰지 않는다 (긴 표현·짧은 표현 모두)
REENTRY_WORDS = ("다시", "재입국", "재차", "새로", "재입")
# 문서 원문에서 기준일 이름 바로 앞에 오면 안 되는 글자 (한글·영문·숫자: "재입국일"의 '입국일' 등)
LABEL_PREFIX_BLOCK = r"(?<![가-힣A-Za-z0-9])"
# 기준일 종류는 LLM 값이 아니라 이 고정 표로 코드가 정한다.
# 종류: (공식 자료 인용문의 "~부터" 앞 표현들, 업로드 문서에서 날짜를 찾을 이름들, LLM base/baseLabels로 허용하는 이름들)
# 비교는 공백을 뺀 글자로 한다. 표에 없는 기준 표현이면 계산하지 않는다 (UNVERIFIED).
# 지원 기준은 의미가 확실한 입국일·체류지 변경일 두 가지만. 고지일(수령일/발송일)·출생일·자격 취득일 등은
# 같은 말이 여러 날을 가리킬 수 있어 지원하지 않는다 (UNVERIFIED, reason에 공식 기준 표시).
BASE_KINDS = {
    "입국일": (
        ["입국한날", "입국일", "외국인이입국한날"],  # 마지막은 정부24 외국인등록 원문 표현 ("체류하게 될 외국인이 입국한 날부터")
        ["입국일", "입국한 날"],
        ["입국일", "입국한 날"],
    ),
    "체류지 변경일": (
        ["체류지를변경한날", "체류지변경일"],
        ["체류지 변경일", "체류지를 변경한 날"],
        ["체류지 변경일", "체류지를 변경한 날"],
    ),
}


@dataclass
class Rule:
    base: str               # LLM이 낸 기준 이름. 검사용일 뿐, 기준일 종류는 BASE_KINDS로 코드가 정함
    labels: list[str]       # LLM이 낸 기준일 이름들. 검사용 (문서 날짜 검색에는 BASE_KINDS 이름만 씀)
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


def quote_base_kind(quote: str) -> str | None:
    """인용문 '~부터/~로부터' 앞 기준 표현으로 기준일 종류를 정한다 (BASE_KINDS 고정 표). 표에 없으면 None.

    기준 구절 = '부터' 앞에서 인용문 시작이나 마지막 문장부호 뒤부터의 전부.
    그 구절 전체(공백 무시)가 표의 표현과 정확히 같아야 한다. 앞에 한글 낱말이 하나라도 붙으면 인정하지 않는다.
    구절에 "다시·재입국·재차·새로" 같은 말이 있으면 다른 입국일 수 있어 None.
    예) "외국인이 입국한 날부터 90일 이내에" → 입국일, "입국한 날로부터 90일 이내" → 입국일
        "다시 한국에 입국한 날부터 …", "재입국한 날부터 …", "최초 입국한 날부터 …", "이 민원은 입국한 날부터 …" → None
    """
    m = BASE_PHRASE_RE.search(quote or "")
    if not m:
        return None
    raw_clause = PHRASE_BREAK_RE.split(m.group(1))[-1].strip()   # 마지막 문장부호 뒤 ~ '부터' 앞
    clause = _squeeze(raw_clause)
    if any(word in clause for word in REENTRY_WORDS):
        return None
    exact = {p: kind for kind, (phrases, _, _) in BASE_KINDS.items() for p in phrases}.get(clause)
    if exact:
        return exact
    # 긴 표현(주어 포함)은 구절 끝에 낱말 시작으로 붙어 있으면 인정 ("재외국인이 입국한 날"은 아님)
    kinds = {kind for kind, patterns in LONG_PHRASE_RES.items() if any(p.search(raw_clause) for p in patterns)}
    return kinds.pop() if len(kinds) == 1 else None


def source_passages(quote: str, source_text: str) -> list[str] | None:
    """공식 자료 원문에서 인용문이 나오는 모든 위치의 '원문 구절' (앞 문맥 + 인용 부분). 쓸 수 없으면 None.

    - 인용문은 공백을 무시하고 찾는다.
    - 원문 구절 = 그 위치 앞쪽의 마지막 문장부호·줄바꿈 다음부터 인용 부분 끝까지.
      (LLM이 "재입국한 날부터"에서 "입국한 날부터"만 잘라 인용해도 원문 문맥으로 검사하려고)
    - 인용 위치 바로 앞 글자가 한글·영문·숫자면(예: "재" + "입국한 날") None.
    - 여러 번 나오면 모든 위치를 돌려주고, 하나라도 앞 글자 검사에 걸리면 None. 한 번도 없으면 None.
    """
    key = _squeeze(quote)
    if not key or not source_text:
        return None
    pattern = re.compile(r"\s*".join(map(re.escape, key)))
    passages = []
    for m in pattern.finditer(source_text):
        start = m.start()
        if start and re.match(r"[가-힣A-Za-z0-9]", source_text[start - 1]):
            return None
        before = source_text[:start]
        cut = max([before.rfind("\n"), *(b.end() - 1 for b in PHRASE_BREAK_RE.finditer(before))])
        passages.append(before[cut + 1:] + m.group(0))
    return passages or None


def base_kind(rule: Rule, source_text: str | None = None) -> str | None:
    """코드가 정한 기준일 종류. LLM의 base·baseLabels가 모두 그 종류의 이름이어야 함 (아니면 None).

    source_text를 주면 인용문이 아니라 공식 원문 구절(source_passages)로 판단한다.
    인용문이 원문에 여러 번 나오면 모든 위치가 같은 종류여야 한다.
    """
    passages = [rule.quote] if source_text is None else source_passages(rule.quote, source_text)
    if not passages:
        return None
    kinds = {quote_base_kind(p) for p in passages}
    if len(kinds) != 1 or None in kinds:
        return None
    kind = kinds.pop()
    allowed = {_squeeze(name) for name in BASE_KINDS[kind][2]}
    if any(_squeeze(name) not in allowed for name in (rule.base, *rule.labels)):
        return None
    return kind


def quote_base_matches(rule: Rule, source_text: str | None = None) -> bool:
    """기준 표현(원문 구절 또는 인용문)이 표에 있고, LLM의 base·baseLabels가 그 기준과 같은지."""
    return base_kind(rule, source_text) is not None


def quote_supports_rule(rule: Rule, source_text: str) -> bool:
    """인용문이 자료 원문에 있고, '기준부터 N단위 이내' 모양이며, 숫자·단위가 rule과 같고,
    원문 구절 기준으로 기준일 종류가 정해지는지 (경계·긴 표현·금지어 검사는 원문 구절로)."""
    if not rule.quote or not source_passages(rule.quote, source_text):
        return False
    if UNSUPPORTED_RE.search(rule.quote):
        return False
    periods = [(int(n), UNIT_WORDS[u]) for n, u in PERIOD_RE.findall(rule.quote)]
    if periods != [(rule.amount, rule.unit)]:  # 기간 표현이 정확히 하나이고 rule과 같아야 함
        return False
    return quote_base_matches(rule, source_text)


def _date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text or "")
    return _date(m[1], m[2] or m[4], m[3] or m[5]) if m else None


def _date_at_start(text: str) -> date | None:
    """text 맨 앞(공백·콜론 뒤)의 날짜. 사이에 다른 글자가 있으면 None."""
    start = LABEL_SEP_RE.match(text).end()
    d = DATE_RE.match(text, start)
    return _date(d[1], d[2] or d[4], d[3] or d[5]) if d else None


def find_base_date(original_text: str, labels: list[str]) -> date | None:
    """원문에서 기준일 이름 바로 뒤(공백·콜론만 사이에 허용)의 날짜. 서로 다른 날짜가 여럿이면 None.

    - 같은 줄: "입국일 2026년 10월 5일", "입국한 날: 2026.10.05"
    - 다음 줄: 이름(+공백·콜론)만 있는 줄 다음 줄 맨 앞의 날짜 (OCR이 표의 칸을 줄로 나눈 경우: "입국일\\n2026년 10월 5일")
    - 안 씀: "입국일: 미상 / 발급일: 2026-10-05" (사이에 다른 값·필드명이 있음)
    - 안 씀: "재입국일 2026-10-05" (이름 바로 앞이 줄 시작·공백·문장부호가 아님 → 다른 이름)
    """
    lines = (original_text or "").splitlines()
    found = set()
    for n, line in enumerate(lines):
        for label in labels:
            for m in re.finditer(LABEL_PREFIX_BLOCK + re.escape(label), line):
                rest = line[m.end():]
                value = _date_at_start(rest)
                if value is None and LABEL_SEP_RE.fullmatch(rest) and n + 1 < len(lines):
                    value = _date_at_start(lines[n + 1].strip())  # 이름만 있는 줄 → 다음 줄 맨 앞 날짜
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
    """비교할 문서 기한.

    원래 기한 목록에서 label이 applies_to와 같은 행을 먼저 고르고, 그 행의 날짜를 해석 못 하면 None
    (다른 기한으로 대체하지 않음). 같은 label 행이 없을 때는 기한이 하나뿐이면 그 행.
    """
    rows = [(_text(d.get("label")), d.get("date")) for d in deadlines or [] if isinstance(d, dict)]
    key = _squeeze(applies_to)
    chosen = [raw for label, raw in rows if key and _squeeze(label) == key]
    if not chosen:
        chosen = [raw for _, raw in rows] if len(rows) == 1 else []
    values = {parse_date(_text(raw)) or _iso(raw) for raw in chosen}
    return values.pop() if len(values) == 1 and None not in values else None


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
    kind = base_kind(rule, source_text)  # 기준일 종류는 코드가 원문 구절로 정함 (quote_supports_rule에서 이미 확인)
    doc_date = pick_document_deadline(deadlines, rule.applies_to)
    if doc_date is None:
        return unverified("공식 기준과 비교할 문서 기한을 하나로 정하지 못했어요")
    # 날짜는 표에 있는 그 기준일 이름으로만 찾음 (LLM의 base·baseLabels로 검색하지 않음)
    base = find_base_date(original_text, BASE_KINDS[kind][1])
    if base is None:
        return unverified(f"문서에서 {_eul(kind)} 찾지 못해 기한을 계산할 수 없어요")

    end = period_end(base, rule.amount, rule.unit)
    official = f"{kind}({base.isoformat()})부터 {rule.amount}{UNITS[rule.unit]} 이내 → {end.isoformat()}"
    if doc_date == end:
        return Judgement("MATCHED", official, f"{official}로 문서 기한과 같아요.", [rule.source_id])
    if doc_date == end - timedelta(days=1):
        return unverified(f"{official}인데 문서 기한은 하루 빨라요. {kind} 당일을 포함해 세면 같을 수 있어요")
    if _maybe_holiday_extension(end, doc_date):
        return unverified(f"{official}인데 그날이 주말이라 기한이 늦춰졌을 수 있어요")
    return Judgement("MISMATCH", official, f"{official}인데 문서 기한은 {doc_date.isoformat()}이에요.",
                     [rule.source_id])
