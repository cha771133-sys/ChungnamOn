"""OCR 원문과 챗봇 입력의 개인정보 마스킹.

pipeline.mask_personal_info()와 chatbot.services.ai_client.prepare_ai_payload()가 이 모듈을 쓴다.

기준: 사람을 알아볼 수 있는 정보는 가리고, 행정 안내에 필요한 기관·납부 정보는 남긴다.
누락보다 과잉 마스킹을 택한다(F1-4).

- 가림 대상별 규칙은 MASKING_RULES에 순서대로 있다. 대상을 빼거나 더할 때는 이 목록만 고친다.
- 같은 글자를 두 번 넣어도 결과가 같아야 한다. 챗봇이 이미 가린 원문을 다시 넣기 때문이다.
- 예외는 잡지 않는다. pipeline이 문서를 FAILED(step=MASKING)로 바꾼다.
- 원문은 예외 메시지와 로그에 넣지 않는다.
"""

import re

REGISTRATION_MARK = '******-*******'
PASSPORT_MARK = '[여권번호 가림]'
ADDRESS_MARK = '[주소 가림]'
ACCOUNT_MARK = '[계좌 가림]'
PHONE_MARK = '[전화번호 가림]'
NAME_MARK = '[이름 가림]'

# 줄 맨 앞의 글머리표·번호 (예: "■ ", "- ")
_BULLET = r'[ \t]*(?:[■□•·\-*][ \t]*)?'

# 기관 안내 줄로 보는 단어. 이 단어가 있는 줄의 주소·전화번호는 남긴다.
AGENCY_WORDS = (
    '시청', '군청', '구청', '도청', '세무서', '세무과', '공단', '지사', '사무소', '센터',
    '출입국', '민원실', '청사', '주민센터', '행정복지센터',
)
AGENCY_LABELS = ('문의', '제출처', '접수처', '담당', '찾아오는', '오시는', '발신', '콜센터', '대표번호', '위치', '기관')
# 기관 단어 바로 뒤에 로·길이 오면 도로명(예: 시청로)이라 기관 단어로 보지 않는다.
_AGENCY_WORD_PATTERN = re.compile(f'(?:{"|".join(AGENCY_WORDS)})(?![로길])')

# 표 형식 문서에서 다음 줄을 값으로 읽을 때, 그 줄이 다른 라벨이면 값이 빈칸인 것으로 본다.
_TABLE_LABELS = (
    r'외국인\s*등록\s*번호', r'주민\s*등록\s*번호', r'여권\s*번호', r'(?:현\s*)?주\s*소(?:\s*지)?', r'거\s*주\s*지',
    r'연\s*락\s*처', r'전화\s*번호', r'휴대\s*(?:전화|폰)(?:\s*번호)?', r'성\s*명', r'이\s*름', r'납세자', r'납부자',
    r'가입자', r'신청인(?:\s*정보)?', r'출금\s*(?:은행|계좌)', r'환급\s*(?:은행|계좌)', r'계좌\s*번호', r'가상\s*계좌',
    r'과세\s*연도', r'세\s*목', r'세\s*액', r'납부\s*기한', r'고지\s*월', r'신청일', r'발행일', r'전자\s*납부\s*번호',
    r'고지서\s*번호', r'합계\s*금액',
)
_TABLE_LABEL_LINE = re.compile(rf'^{_BULLET}(?:{"|".join(_TABLE_LABELS)})[ \t]*[:：]?[ \t]*$')

# ── 외국인등록번호 ─────────────────────────────────────────────
# 숫자 6자리와 7자리. 공백으로 쪼개지거나 하이픈이 없어도 찾는다(ocr.py와 같은 기준).
# 하이픈 바로 앞이나 뒤에서 줄이 바뀐 경우("900101-" 다음 줄 "5123456")도 잇는다.
# 하이픈 없이 줄만 바뀐 두 숫자는 서로 다른 값일 수 있어 잇지 않는다.
_DASH = '[-‐–—−]'
REGISTRATION_PATTERN = re.compile(
    rf'(?<!\d)\d(?:[ \t]?\d){{5}}'
    rf'(?:[ \t]*{_DASH}[ \t]*\n?[ \t]*|[ \t]*\n[ \t]*{_DASH}[ \t]*|[ \t]*)'
    rf'\d(?:[ \t]?\d){{6}}(?!\d)'
)
REGISTRATION_LABEL = re.compile(
    rf'^(?P<label>{_BULLET}(?:외국인\s*등록\s*번호|주민\s*등록\s*번호)[ \t]*[:：]?[ \t]*)(?P<value>.*)$'
)

# ── 여권번호 ──────────────────────────────────────────────────
PASSPORT_LABEL = re.compile(
    rf'^(?P<label>{_BULLET}(?:여권\s*번호|passport(?:\s*(?:no\.?|number))?)[ \t]*[:：]?[ \t]*)(?P<value>.*)$',
    re.IGNORECASE,
)

# ── 주소 ─────────────────────────────────────────────────────
# 시·도와 시·군·구(시 아래 구 포함)까지 남기고 그 뒤를 가린다.
_SIDO = (
    r'(?:[가-힣]{2,6}(?:특별자치도|특별자치시|특별시|광역시|도)'
    r'|서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)'
)
_SIGUNGU = r'[가-힣]{1,6}(?:시|군|구)'
_REGION = rf'(?:(?:{_SIDO}[ \t]+)?{_SIGUNGU}(?:[ \t]+[가-힣]{{1,6}}구)?|[가-힣]{{2,6}}특별자치시)'
# 읍·면·동·리, 도로명(…로·…길), 번지 숫자
_DETAIL_START = r'(?:[가-힣0-9]+(?:읍|면|동|리|로|길)(?=[ \t\d,]|$)|\d)'
REGION_PREFIX = re.compile(rf'^[ \t]*(?P<keep>{_REGION})(?=[ \t,]|$)(?P<rest>.*)$')
# 주소 다음 줄로 이어진 상세주소: 읍·면·동·도로명으로 시작하거나 "101동 1001호"처럼 동·호·층·번지로 시작한다.
# "2026년 8월 31일" 같은 날짜 줄은 이어진 주소로 보지 않는다.
DETAIL_LINE = re.compile(
    r'^[ \t]*(?:[가-힣0-9]+(?:읍|면|동|리|로|길)(?=[ \t\d,]|$)|\d+(?:-\d+)?(?:번지|동|호|층)(?=[ \t,]|$))'
)
# 라벨 없이 줄 안에 나온 주소: 시·군·구 + 읍·면·동·도로명 + 숫자
ADDRESS_IN_LINE = re.compile(
    rf'(?:(?<=[ \t:：(])|^)(?P<keep>{_REGION})[ \t]+(?P<rest>{_DETAIL_START}[^\n]*\d[^\n]*)$'
)
# 정확한 주소 라벨. 콜론이 있으면 값 형태와 관계없이 가린다.
ADDRESS_LABEL = re.compile(
    rf'^(?P<label>{_BULLET}(?:현\s*)?(?:주\s*소(?:\s*지)?|거\s*주\s*지)[ \t]*)(?P<colon>[:：]?)[ \t]*(?P<value>.*)$'
)
# 잘못 읽힌 주소 라벨(예: "족소:"). 콜론이 있고 값이 주소 형태일 때만 가린다.
FUZZY_ADDRESS_LABEL = re.compile(rf'^(?P<label>{_BULLET}(?:[가-힣]\s*소|주\s*[가-힣])[ \t]*[:：][ \t]*)(?P<value>.+)$')

# ── 개인 계좌 ─────────────────────────────────────────────────
# 출금·환급 등 개인 계좌 라벨은 예금주 표기와 관계없이 가린다.
PERSONAL_ACCOUNT_LABEL = re.compile(
    rf'^(?P<label>{_BULLET}(?:출금|환급|본인|신청인|자동\s*이체)\s*계좌(?:\s*번호)?[ \t]*[:：]?[ \t]*)(?P<value>.*)$'
)
# 일반 "계좌번호" 라벨은 납부용 가상계좌이거나 예금주가 기관일 때만 남긴다.
GENERIC_ACCOUNT_LABEL = re.compile(rf'^(?P<label>{_BULLET}계좌\s*번호[ \t]*[:：]?[ \t]*)(?P<value>.*)$')
ACCOUNT_NUMBER = re.compile(r'\d[\d \t\-]{6,}\d')
PAYMENT_ACCOUNT_WORDS = ('가상계좌', '가상 계좌', '납부')
AGENCY_HOLDER = re.compile(
    r'예금주[ \t]*[:：]?[ \t]*[가-힣]*(?:특별자치도|특별자치시|특별시|광역시|도|시|군|구|청|공단|공사|세무서|센터|사무소|지사)(?=[ \t)\],]|$)'
)

# ── 개인 연락처 ───────────────────────────────────────────────
# 휴대전화 번호는 어디에 있든 가린다. 일반 전화번호는 연락처 라벨이 있을 때만 가린다.
MOBILE_PATTERN = re.compile(r'(?<!\d)(?:\+82[- ]?1[016789]|01[016789])[- .]?\d{3,4}[- .]?\d{4}(?!\d)')
LANDLINE_PATTERN = re.compile(r'(?<!\d)0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)')
PHONE_LABEL = re.compile(
    rf'^(?P<label>{_BULLET}(?:연\s*락\s*처|전화\s*번호|휴대\s*(?:전화|폰)(?:\s*번호)?|핸드폰(?:\s*번호)?|전화)[ \t]*[:：]?[ \t]*)'
    rf'(?P<value>.*)$'
)

# ── 이름 ─────────────────────────────────────────────────────
# 라벨 뒤의 이름과 괄호 안 영문 이름까지 값 전체를 가린다.
NAME_LABEL = re.compile(
    rf'^(?P<label>{_BULLET}(?:납세자|납부자|가입자|신청인|성\s*명|이\s*름)(?:[ \t]*(?:성명|이름))?)'
    rf'(?P<sep>[ \t]*[:：][ \t]*|[ \t]+|[ \t]*$)(?P<value>.*)$'
)
# 이름과 같은 줄에 이어 나온 다른 라벨(예: "성명: 홍길동 연락처: …"). 이름은 이 라벨 앞에서 끝난다.
INLINE_NEXT_LABEL = re.compile(
    r'[ \t]+(?=(?:연\s*락\s*처|전화\s*번호|휴대\s*(?:전화|폰)|주\s*소|외국인\s*등록\s*번호|주민\s*등록\s*번호|여권\s*번호'
    r'|생년월일|국적|계좌\s*번호|출금|환급|이메일)[ \t]*[:：])'
)
# 같은 줄에 이어진 다음 항목의 라벨(예: "… 납부기한: 2026년 8월 31일", "… 세액: 6,250원").
# 값을 가릴 때 이 라벨 앞에서 멈춰 뒤의 기한·금액을 남긴다. 이름 조각("반 안")을 라벨로 오해하지 않도록
# 라벨 낱말은 두 글자 이상이고, 두 낱말 라벨은 앞 낱말이 아래 목록일 때만 인정한다(예: "납부 기한:").
_TWO_WORD_LABEL_HEADS = '납부|출금|환급|신청|납기|고지|휴대|외국인|주민|여권|과세|합계|전자|문의|담당|발행|처리'
NEXT_FIELD_LABEL = re.compile(rf'[ \t]+(?=(?:(?:{_TWO_WORD_LABEL_HEADS})[ \t]+)?[가-힣]{{2,10}}[ \t]*[:：])')
# 라벨 뒤에 이름이 아니라 이런 단어가 오면 표 머리글로 본다(예: "신청인 정보").
NOT_NAME_WORDS = ('정보', '성명', '이름', '서명', '구분', '현황', '내역', '본인', '확인', '번호', '주소', '연락처')
# 문장 끝. 이름이 "다"·"요"로 끝날 수 있어 한 글자 어미는 넣지 않는다.
_SENTENCE_END = re.compile(r'(?:[?？!.。]|니다|세요|나요|까요|어요|아요|해요|데요|네요|군요)[ \t)]*$')
_ALL_MARKS = (REGISTRATION_MARK, PASSPORT_MARK, ADDRESS_MARK, ACCOUNT_MARK, PHONE_MARK, NAME_MARK)


def _agency_line(line):
    return bool(_AGENCY_WORD_PATTERN.search(line)) or any(label in line for label in AGENCY_LABELS)


def _is_table_label(line):
    return bool(_TABLE_LABEL_LINE.match(line))


def _next_value_index(lines, index):
    """라벨만 있는 줄 다음의 값 줄 번호. 다음 줄이 없거나 비었거나 다른 라벨이면 None."""
    following = index + 1
    if following >= len(lines) or not lines[following].strip() or _is_table_label(lines[following]):
        return None
    return following


def _labeled_lines(lines, label_pattern, mask_value, mask_next=None):
    """라벨 줄을 찾아 같은 줄의 값이나 다음 줄의 값을 가린다. 가린 항목 수를 돌려준다.

    mask_value(value) → (새 값, 가린 수). mask_next가 없으면 다음 줄에도 mask_value를 쓴다.
    """
    mask_next = mask_next or mask_value
    count = 0
    for index, line in enumerate(lines):
        match = label_pattern.match(line)
        if not match:
            continue
        value = match.group('value')
        if value.strip():
            new_value, masked = mask_value(value)
            if masked:
                lines[index] = match.group('label') + new_value
                count += masked
            continue
        following = _next_value_index(lines, index)
        if following is not None:
            new_line, masked = mask_next(lines[following])
            if masked:
                lines[following] = new_line
                count += masked
    return count


def _digit_count(text):
    return sum(char.isdigit() for char in text)


def _split_at_next_field(value):
    """값을 (이 항목의 값, 같은 줄에 이어진 다음 항목)으로 나눈다. 다음 항목이 없으면 뒤는 빈 문자열."""
    candidates = [match.start() for match in (NEXT_FIELD_LABEL.search(value), INLINE_NEXT_LABEL.search(value)) if match]
    if not candidates:
        return value, ''
    cut = min(candidates)
    return value[:cut], value[cut:]


# ── 규칙 ─────────────────────────────────────────────────────

def mask_registration_numbers(lines):
    """외국인등록번호. 패턴으로 찾고, 라벨 뒤 숫자를 잘못 읽은 값도 가린다."""
    text, count = REGISTRATION_PATTERN.subn(REGISTRATION_MARK, '\n'.join(lines))
    lines[:] = text.split('\n')

    def mask_value(value):
        # 숫자 일부를 글자로 잘못 읽어도(예: 9OO101) 숫자가 6개 이상이면 번호로 본다.
        # 같은 줄에 이어진 다음 항목(예: "세액: 6,250원")은 남긴다.
        number, rest = _split_at_next_field(value)
        if REGISTRATION_MARK in number or _digit_count(number) < 6:
            return value, 0
        return REGISTRATION_MARK + rest, 1

    return count + _labeled_lines(lines, REGISTRATION_LABEL, mask_value)


def mask_passport_numbers(lines):
    """여권번호. 라벨이 있는 값만 가린다."""
    def mask_value(value):
        # 같은 줄에 이어진 다음 항목(예: "납부기한: …")은 남긴다.
        number, rest = _split_at_next_field(value)
        if PASSPORT_MARK in number or not any(char.isdigit() for char in number):
            return value, 0
        return PASSPORT_MARK + rest, 1

    return _labeled_lines(lines, PASSPORT_LABEL, mask_value)


def _mask_address_value(value, *, require_address_form):
    """주소 값의 시·도와 시·군·구를 남기고 나머지를 가린다. → (새 값, 가린 수, 상세주소가 다음 줄에 있을 수 있는지)"""
    if ADDRESS_MARK in value:
        return value, 0, False
    region = REGION_PREFIX.match(value)
    if region:
        rest = region.group('rest')
        if rest.strip():
            if require_address_form and not re.match(rf'[ \t,]*{_DETAIL_START}', rest):
                return value, 0, False
            return f'{region.group("keep")} {ADDRESS_MARK}', 1, False
        # 시·군·구까지만 있으면 가릴 것이 없다. 줄이 바뀌어 상세주소가 다음 줄에 있을 수 있다.
        return value, 0, True
    if require_address_form:
        return value, 0, False
    # 시·군·구를 찾지 못하면 주소 전체를 가린다.
    return ADDRESS_MARK, 1, False


def _mask_detail_lines(lines, start):
    """start 줄부터 상세주소 모양의 줄을 다음 필드 전까지 가린다. 하나라도 가렸으면 True."""
    index = start
    while (index < len(lines) and DETAIL_LINE.match(lines[index]) and ADDRESS_MARK not in lines[index]
           and not _is_table_label(lines[index])):
        lines[index] = ADDRESS_MARK
        index += 1
    return index > start


def mask_addresses(lines):
    """개인 주소. 라벨(띄어 쓴 라벨·잘못 읽은 라벨 포함)과 줄 안의 주소 형태로 찾는다."""
    count = 0
    for index, line in enumerate(lines):
        if ADDRESS_MARK in line:
            continue
        match = ADDRESS_LABEL.match(line)
        # 콜론 없는 라벨과 잘못 읽은 라벨은 값이 주소 형태일 때만 가린다("주소 변경은 어떻게 하나요?").
        require_form = bool(match) and not match.group('colon')
        if not match:
            match = FUZZY_ADDRESS_LABEL.match(line)
            require_form = True
        if not match:
            continue
        # 개인 주소 라벨이 있으면 도로명 등에 기관 단어가 있어도 가린다. 기관 주소는 제출처·문의 등 기관 라벨 줄로 남긴다.
        value = match.group('value')
        value_index = index
        # 같은 줄에 이어진 다음 항목(예: "납부기한: …")은 주소가 아니므로 남긴다.
        if value.strip():
            address, rest = _split_at_next_field(value)
            new_value, masked, detail_may_follow = _mask_address_value(address, require_address_form=require_form)
            new_line = line[:match.start('value')] + new_value + rest
        else:
            # 라벨만 있는 줄: 다음 줄이 값이다.
            value_index = _next_value_index(lines, index)
            if value_index is None:
                continue
            address, rest = _split_at_next_field(lines[value_index])
            new_value, masked, detail_may_follow = _mask_address_value(address, require_address_form=False)
            new_line = new_value + rest
        if masked:
            lines[value_index] = new_line
            count += masked
            # 상세주소가 다음 줄로 이어지면 다음 필드 전까지 함께 가린다. 같은 주소라 개수는 늘리지 않는다.
            _mask_detail_lines(lines, value_index + 1)
        elif detail_may_follow and _mask_detail_lines(lines, value_index + 1):
            # 시·군·구 뒤에서 줄이 바뀐 주소: 다음 줄의 상세주소를 가린다.
            count += 1

    # 라벨 없이 나온 개인 주소. 기관 안내 줄은 남긴다.
    for index, line in enumerate(lines):
        if ADDRESS_MARK in line or _agency_line(line):
            continue
        match = ADDRESS_IN_LINE.search(line)
        if match:
            _, rest = _split_at_next_field(line[match.start('rest'):])
            lines[index] = line[:match.start('rest')] + ADDRESS_MARK + rest
            count += 1
    return count


def mask_accounts(lines):
    """개인 계좌번호. 출금·환급 등 개인 계좌 라벨은 항상 가리고, 일반 계좌번호 라벨은 납부용 계좌만 남긴다."""
    def mask_personal(value):
        # 예금주 표기는 기관 계좌라는 근거가 아니다.
        return ACCOUNT_NUMBER.subn(ACCOUNT_MARK, value)

    def mask_generic(value):
        if any(word in value for word in PAYMENT_ACCOUNT_WORDS) or AGENCY_HOLDER.search(value):
            return value, 0
        return ACCOUNT_NUMBER.subn(ACCOUNT_MARK, value)

    return (_labeled_lines(lines, PERSONAL_ACCOUNT_LABEL, mask_personal)
            + _labeled_lines(lines, GENERIC_ACCOUNT_LABEL, mask_generic))


def mask_phone_numbers(lines):
    """개인 연락처. 휴대전화 번호 전체와, 연락처 라벨이 붙은 일반 전화번호를 가린다."""
    count = 0
    for index, line in enumerate(lines):
        lines[index], masked = MOBILE_PATTERN.subn(PHONE_MARK, line)
        count += masked

    def mask_value(value):
        if _agency_line(value):
            return value, 0
        return LANDLINE_PATTERN.subn(PHONE_MARK, value)

    return count + _labeled_lines(lines, PHONE_LABEL, mask_value)


def _looks_like_name(value):
    """라벨과 같은 줄에 콜론 없이 온 값이 이름인지. 문장이나 표 머리글이면 이름이 아니다."""
    stripped = value.strip()
    if not stripped or len(stripped) > 40 or any(char.isdigit() for char in stripped):
        return False
    if stripped.split()[0] in NOT_NAME_WORDS or _SENTENCE_END.search(stripped):
        return False
    return len(stripped.split()) <= 6


def _name_parts(name):
    """찾은 이름에서 다른 곳에서도 가릴 부분: 전체, 괄호 앞 이름, 괄호 안 영문 이름."""
    parts = {name.strip()}
    before, _, inside = name.partition('(')
    parts.add(before.strip())
    parts.add(inside.rstrip(') \t').strip())
    # 한 글자 조각은 다른 낱말까지 가릴 수 있어 쓰지 않는다. 두 글자는 _name_pattern()의 경계 조건으로 제한한다.
    return sorted((part for part in parts if len(part.replace(' ', '')) >= 2), key=len, reverse=True)


def _name_pattern(part):
    """다른 곳에 다시 나온 이름을 찾는 정규식. 두 글자 이름은 다른 낱말의 일부일 때 가리지 않는다."""
    body = r'[ \t]+'.join(re.escape(word) for word in part.split())
    if len(part.replace(' ', '')) >= 3:
        return re.compile(body)
    # 예: "이안 님께", "이안님", "이안씨"는 가리고 "이안경", "보이안"은 가리지 않는다.
    return re.compile(rf'(?<![가-힣A-Za-z]){body}(?:(?![가-힣A-Za-z])|(?=님|씨))')


def _mask_other_fields(text):
    """이름 뒤에 이어진 같은 줄의 다른 항목을 이름 외 규칙으로 가린다. → (새 글자, 가린 수)"""
    if not text.strip():
        return text, 0
    stripped = text.lstrip()
    lines = [stripped]
    count = sum(rule(lines) for rule in MASKING_RULES if rule is not mask_names)
    return text[:len(text) - len(stripped)] + '\n'.join(lines), count


def mask_names(lines):
    """이름. 라벨 뒤의 이름(괄호 안 영문 이름 포함) 전체를 가리고, 같은 이름이 다른 곳에 나오면 함께 가린다."""
    count = 0
    found = []
    for index, line in enumerate(lines):
        match = NAME_LABEL.match(line)
        if not match:
            continue
        value = match.group('value')
        has_colon = ':' in match.group('sep') or '：' in match.group('sep')
        if value.strip():
            # 같은 줄에 다른 라벨이 이어지면 이름은 그 앞에서 끝난다(예: "연락처:", "납부기한:").
            name, rest = _split_at_next_field(value)
            # 이름 자체가 이미 가려졌는지만 본다. 뒤에 이어진 다른 항목의 가림 표시는 상관없다.
            if not name.strip() or any(mark in name for mark in _ALL_MARKS) \
                    or (not has_colon and not _looks_like_name(name)):
                continue
            found.append(name)
            # 뒤에 남기는 부분도 다른 규칙으로 가린다. 줄 맨 앞 라벨만 보는 규칙(계좌·일반 전화·여권번호)은
            # "성명: 홍길동 계좌번호: …"처럼 줄 중간에 온 라벨을 앞에서 지나쳤기 때문이다.
            rest, rest_count = _mask_other_fields(rest)
            lines[index] = match.group('label') + match.group('sep') + NAME_MARK + rest
            count += 1 + rest_count
            continue
        following = _next_value_index(lines, index)
        if following is None:
            continue
        candidate = lines[following]
        # 다른 가림 표시나 숫자가 있으면 이름이 아니다. 표의 값 칸이라 문장 끝 검사는 하지 않는다.
        if any(mark in candidate for mark in _ALL_MARKS) or any(char.isdigit() for char in candidate):
            continue
        found.append(candidate)
        lines[following] = NAME_MARK
        count += 1

    parts = sorted({part for name in found for part in _name_parts(name)}, key=len, reverse=True)
    for part in parts:
        pattern = _name_pattern(part)
        for index, line in enumerate(lines):
            lines[index], masked = pattern.subn(NAME_MARK, line)
            count += masked
    return count


# 순서가 중요하다: 번호를 먼저 가려야 주소·계좌·연락처 규칙이 그 숫자를 다시 보지 않는다.
MASKING_RULES = (
    mask_registration_numbers,
    mask_passport_numbers,
    mask_addresses,
    mask_accounts,
    mask_phone_numbers,
    mask_names,
)


def mask_text(text):
    """text의 개인정보를 가린다. pipeline.mask_personal_info() 계약과 같은 형식을 돌려준다."""
    if not isinstance(text, str):
        raise TypeError('마스킹할 값은 문자열이어야 합니다.')
    lines = text.split('\n')
    count = sum(rule(lines) for rule in MASKING_RULES)
    return {'masked_text': '\n'.join(lines), 'masked_count': count}
