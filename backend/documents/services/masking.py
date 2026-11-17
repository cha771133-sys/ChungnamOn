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
from contextvars import ContextVar

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
    r'고지서\s*번호', r'합계\s*금액', r'수신(?:인|자)?', r'받는\s*(?:사람|분)', r'대상자',
    r'(?:영문|한글)\s*(?:성\s*명|이\s*름|명)', r'(?:성\s*명|이\s*름)\s*\((?:영문|한글)\)',
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
# 이름표로 보아 등록번호가 아닌 것이 분명한 번호. 하이픈 없는 13자리 숫자가 이 이름표 바로 뒤(같은 줄)나
# 이름표만 있는 줄의 바로 다음 줄에 있으면 가리지 않는다. 납부에 필요한 번호를 사용자가 볼 수 있어야 한다.
# 이름표가 없거나 목록에 없는 이름표면 지금처럼 가린다(F1-4, 애매하면 가린다).
_OTHER_NUMBER_LABEL = r'(?:고지\s*서?|전자\s*납부|납부|문서|관리|접수)\s*번호'
_OTHER_NUMBER_LABEL_BEFORE = re.compile(rf'{_OTHER_NUMBER_LABEL}[ \t]*[:：]?[ \t]*$')
_OTHER_NUMBER_LABEL_LINE = re.compile(rf'^{_BULLET}{_OTHER_NUMBER_LABEL}[ \t]*[:：]?[ \t]*$')

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
# 건물 이름으로 시작하는 상세주소 줄: "가상아파트 101동 1001호", "견본빌라 302호", "행복오피스텔 5층 501호".
# 줄 전체가 "건물 이름(세 낱말까지) + 동·호·층 숫자"여야 한다. 뒤에 다른 말이 이어지는 줄은 주소가 아니다:
# "제출기한 2026년 8월 31일 (본관 1층)", "시청 본관 2층 세무과로 방문해 주시기 바랍니다."
BUILDING_LINE = re.compile(
    r'^[ \t]*(?:(?!\d+[ \t]*[년월일원])[가-힣A-Za-z0-9·()\-]+[ \t]+){0,3}'
    # OCR이 띄어쓰기를 빼면 건물 이름과 숫자가 붙는다: "견본빌라302호"
    r'(?:[가-힣A-Za-z·]+)?'
    r'(?:(?:제|지하|B)?\d+(?:-\d+)?[ \t]*(?:동|호|층)[ \t,]*)+$'
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
    rf'^(?P<label>{_BULLET}(?:납세자|납부자|가입자|신청인|성\s*명|이\s*름'
    # 영문명·한글명처럼 이름을 따로 적는 칸: "영문명: HONG GILDONG", "성명(영문): HONG GILDONG"
    rf'|(?:영문|한글)\s*(?:성\s*명|이\s*름|명)'
    rf'|(?P<recipient>수신(?:인|자)?|받는\s*(?:사람|분)|대상자))(?:[ \t]*(?:성명|이름))?(?:[ \t]*\((?:영문|한글)\))?)'
    rf'(?P<sep>[ \t]*[:：][ \t]*|[ \t]+|[ \t]*$)(?P<value>.*)$'
)
# 받는 사람 라벨(수신·받는 사람·대상자) 뒤에는 사람이 아니라 기관이나 직위가 올 수 있다(예: "수신: 아산시청",
# "수신: 아산시장"). 값을 낱말로 나눠 기관·직위·높임말만 남기고 나머지는 이름으로 보고 가린다
# (예: "홍길동 센터장 귀하" → "[이름 가림] 센터장 귀하"). 판단은 _mask_recipient_value()에 있다.
_RECIPIENT_TITLE = re.compile(
    r'(?:시장|군수|구청장|도지사|장관|청장|서장|소장|지사장|이사장|센터장|위원장|본부장|원장'
    r'|주무관|사무관|서기관|담당관|조사관|과장|팀장|국장|실장|계장|부장|차장|대리|대표|대표이사|담당자)$'
)
# AGENCY_WORDS에 없는 기관 이름. 받는 사람 값에서만 쓴다(기관 안내 줄 판단에는 영향을 주지 않는다).
# "부"로 끝나는 낱말을 모두 기관으로 보면 이름(예: 김영부)을 놓치므로 부처 이름을 하나씩 적는다.
_RECIPIENT_AGENCY = re.compile(
    r'(?:법무|고용노동|행정안전|보건복지|외교|교육|국토교통|기획재정|여성가족|산업통상자원|문화체육관광|환경|국방|통일'
    r'|농림축산식품|해양수산|중소벤처기업|과학기술정보통신)부'
    r'|국세청|관세청|경찰청|경찰서|소방서|검찰청|교육청|보건소|우체국|법원|위원회'
)
_RECIPIENT_HONORIFICS = ('귀하', '님', '씨')
# "수신: 수신자 참조"는 받는 사람이 따로 적혀 있다는 뜻이다.
_RECIPIENT_REFERENCE = re.compile(r'^[ \t]*(?:수신(?:인|자)?[ \t]*)?참조[ \t]*$')
# 실제 지역 이름. 생김새(…도, …시, …구)로 판단하면 이름(김진도, 박진구)을 지역으로 잘못 본다.
# 목록과 완전히 같은 낱말만 지역으로 보고, 그것도 뒤에 기관 이름이 이어질 때만 남긴다.
# 동·리 이름은 목록으로 만들 수 없고 이름(홍길동)과 구분할 수 없어 지역으로 보지 않는다.
_REGION_NAMES = frozenset('''
서울특별시 부산광역시 대구광역시 인천광역시 광주광역시 대전광역시 울산광역시 세종특별자치시
경기도 강원특별자치도 강원도 충청북도 충청남도 전북특별자치도 전라북도 전라남도 경상북도 경상남도
제주특별자치도 제주도 서울 서울시 부산시 대구시 인천시 대전시 울산시 세종시 충북 충남 전북 전남 경북 경남
수원시 성남시 의정부시 안양시 부천시 광명시 평택시 동두천시 안산시 고양시 과천시 구리시 남양주시 오산시
시흥시 군포시 의왕시 하남시 용인시 파주시 이천시 안성시 김포시 화성시 광주시 양주시 포천시 여주시
춘천시 원주시 강릉시 동해시 태백시 속초시 삼척시 청주시 충주시 제천시
천안시 공주시 보령시 아산시 서산시 논산시 계룡시 당진시
전주시 군산시 익산시 정읍시 남원시 김제시 목포시 여수시 순천시 나주시 광양시
포항시 경주시 김천시 안동시 구미시 영주시 영천시 상주시 문경시 경산시
창원시 진주시 통영시 사천시 김해시 밀양시 거제시 양산시 제주시 서귀포시
강화군 옹진군 기장군 달성군 군위군 울주군 연천군 가평군 양평군
홍천군 횡성군 영월군 평창군 정선군 철원군 화천군 양구군 인제군 고성군 양양군
보은군 옥천군 영동군 증평군 진천군 괴산군 음성군 단양군
금산군 부여군 서천군 청양군 홍성군 예산군 태안군
완주군 진안군 무주군 장수군 임실군 순창군 고창군 부안군
담양군 곡성군 구례군 고흥군 보성군 화순군 장흥군 강진군 해남군 영암군 무안군 함평군 영광군 장성군
완도군 진도군 신안군
의성군 청송군 영양군 영덕군 청도군 고령군 성주군 칠곡군 예천군 봉화군 울진군 울릉군
의령군 함안군 창녕군 남해군 하동군 산청군 함양군 거창군 합천군
종로구 중구 용산구 성동구 광진구 동대문구 중랑구 성북구 강북구 도봉구 노원구 은평구 서대문구 마포구 양천구
강서구 구로구 금천구 영등포구 동작구 관악구 서초구 강남구 송파구 강동구
서구 동구 남구 북구 영도구 부산진구 동래구 해운대구 사하구 금정구 연제구 수영구 사상구 수성구 달서구
미추홀구 연수구 남동구 부평구 계양구 광산구 유성구 대덕구
장안구 권선구 팔달구 영통구 수정구 중원구 분당구 만안구 동안구 원미구 소사구 오정구 상록구 단원구
덕양구 일산동구 일산서구 처인구 기흥구 수지구 상당구 서원구 흥덕구 청원구 동남구 서북구 완산구 덕진구
의창구 성산구 마산합포구 마산회원구 진해구
'''.split())
# 읍·면 이름. 지역 이름 바로 뒤에 올 때만 지역으로 본다(예: "아산시 배방읍").
_TOWN_WORD = re.compile(r'^[가-힣0-9]+(?:읍|면)$')
# 자주 쓰는 부서 이름. 직위 앞에 붙거나(민원팀장, 총무과장) 과·팀 등이 붙으면(민원팀) 이름이 아니라 부서로 본다.
# 사람 이름으로도 쓰이는 낱말(지원, 세정 등)은 넣지 않는다.
_DEPARTMENT_NAMES = frozenset('''
민원 민원봉사 종합민원 총무 행정 자치행정 기획 기획예산 예산 재무 회계 세무 징수 부과 복지 복지정책 사회복지
노인복지 여성가족 보건 건강 위생 환경 건설 건축 도시 교통 안전 안전총괄 경제 일자리 농업 농정 문화 관광 체육
교육 인사 감사 홍보 정보통신 토지 주택 허가 체류 체류관리 출입국 외국인 이민정보 조사 심사 관리 운영 고객
고객지원 자격 보험료 급여
'''.split())
_DEPARTMENT_WORD = re.compile(r'^(?P<name>[가-힣]+?)(?:과|팀|실|국|계|부|담당)$')
# 이름 끝에 붙여 쓴 높임말(홍길동님). 다른 곳의 같은 이름을 찾을 때는 떼고 찾는다.
_ATTACHED_HONORIFIC = re.compile(r'[ \t]*(?:귀하|님|씨)$')
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


# mask_text() 한 번의 실행에서 찾은 이름 목록. 요청(스레드)마다 따로 유지된다.
_found_names = ContextVar('masking_found_names', default=None)


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


def _split_at_first_mark(value):
    """값을 (첫 가림 표시 앞부분, 가림 표시부터 끝까지)로 나눈다. 앞부분 끝의 띄어쓰기는 뒤로 넘긴다.

    "홍길동 [전화번호 가림]"처럼 이름 뒤에 붙은 번호가 먼저 가려진 값에서 이름만 꺼낼 때 쓴다.
    가림 표시가 없으면 뒤는 빈 문자열이다.
    """
    positions = [value.find(mark) for mark in _ALL_MARKS if mark in value]
    if not positions:
        return value, ''
    head = value[:min(positions)]
    return head.rstrip(), value[len(head.rstrip()):]


# 가려진 번호 뒤에 괄호로 적힌 영문 이름: "홍길동 [전화번호 가림] (HONG GILDONG)"
_PAREN_LATIN_NAME = re.compile(r"[ \t]*\(\s*([A-Za-z][A-Za-z .,'\-]{1,60}?)\s*\)")


def _take_latin_names(marked):
    """가림 표시 뒤에 남은 글자에서 괄호 안 영문 이름을 뺀다. (남은 글자, 찾은 영문 이름 목록)"""
    return _PAREN_LATIN_NAME.sub('', marked), [match.group(1) for match in _PAREN_LATIN_NAME.finditer(marked)]


def _split_at_next_field(value):
    """값을 (이 항목의 값, 같은 줄에 이어진 다음 항목)으로 나눈다. 다음 항목이 없으면 뒤는 빈 문자열."""
    candidates = [match.start() for match in (NEXT_FIELD_LABEL.search(value), INLINE_NEXT_LABEL.search(value)) if match]
    if not candidates:
        return value, ''
    cut = min(candidates)
    return value[:cut], value[cut:]


def is_labeled_other_number(text, start, number):
    """text[start:]에서 찾은 number가 이름표로 보아 등록번호가 아닌 번호(고지번호 등)인지.

    ocr.normalize_registration_numbers()도 같은 기준을 쓴다. 기준이 다르면 고지번호에 하이픈이 들어간다.
    하이픈이 있거나 줄이 바뀐 번호는 등록번호 모양이라 이름표와 관계없이 등록번호로 본다.
    """
    if re.search(rf'{_DASH}|\n', number):
        return False
    line_start = text.rfind('\n', 0, start) + 1
    before = text[line_start:start]
    if before.strip():
        return bool(_OTHER_NUMBER_LABEL_BEFORE.search(before))
    if line_start == 0:
        return False
    # 줄 맨 앞의 번호: 바로 윗줄이 이름표만 있는 줄일 때(표 형식)
    previous_start = text.rfind('\n', 0, line_start - 1) + 1
    return bool(_OTHER_NUMBER_LABEL_LINE.match(text[previous_start:line_start - 1]))


# ── 규칙 ─────────────────────────────────────────────────────

def mask_registration_numbers(lines):
    """외국인등록번호. 패턴으로 찾고, 라벨 뒤 숫자를 잘못 읽은 값도 가린다."""
    joined = '\n'.join(lines)
    count = 0

    def replace(match):
        nonlocal count
        if is_labeled_other_number(joined, match.start(), match.group(0)):
            return match.group(0)
        count += 1
        return REGISTRATION_MARK

    lines[:] = REGISTRATION_PATTERN.sub(replace, joined).split('\n')

    def mask_value(value):
        # 숫자 일부를 글자로 잘못 읽어도(예: 9OO101) 숫자가 6개 이상이면 번호로 본다.
        # 같은 줄에 이어진 다음 항목(예: "세액: 6,250원")은 남긴다.
        number, rest = _split_at_next_field(value)
        rest, rest_count = _mask_other_fields(rest)
        if REGISTRATION_MARK in number or _digit_count(number) < 6:
            return number + rest, rest_count
        return REGISTRATION_MARK + rest, 1 + rest_count

    return count + _labeled_lines(lines, REGISTRATION_LABEL, mask_value)


def mask_passport_numbers(lines):
    """여권번호. 라벨이 있는 값만 가린다."""
    def mask_value(value):
        # 같은 줄에 이어진 다음 항목(예: "납부기한: …")은 남긴다.
        number, rest = _split_at_next_field(value)
        rest, rest_count = _mask_other_fields(rest)
        if PASSPORT_MARK in number or not any(char.isdigit() for char in number):
            return number + rest, rest_count
        return PASSPORT_MARK + rest, 1 + rest_count

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


def _mask_detail_lines(lines, start, building_only=False):
    """start 줄부터 상세주소 모양의 줄을 다음 필드 전까지 가린다. 하나라도 가렸으면 True.

    building_only: 줄 전체가 동·호·층 숫자(건물 이름 포함)인 줄만 가린다. 주소 이름표 없이 찾은 주소 뒤에서 쓴다.
    """
    index = start
    while (index < len(lines)
           and (BUILDING_LINE.match(lines[index]) or (not building_only and DETAIL_LINE.match(lines[index])))
           and ADDRESS_MARK not in lines[index] and not _is_table_label(lines[index])):
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
        # 남기는 뒤 항목에도 개인정보가 있을 수 있다(예: "… 여권번호: M12345678").
        masked_rest, rest_count = _mask_other_fields(rest)
        if rest_count:
            new_line = new_line[:len(new_line) - len(rest)] + masked_rest
            lines[value_index] = new_line
            count += rest_count
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
            rest, rest_count = _mask_other_fields(rest)
            lines[index] = line[:match.start('rest')] + ADDRESS_MARK + rest
            count += 1 + rest_count
            # 이름표 없는 주소도 다음 줄로 이어질 수 있다("견본빌라 302호"). 같은 주소라 개수는 늘리지 않는다.
            # 이름표가 없어 근거가 약하므로 줄 전체가 동·호·층 모양일 때만 가린다.
            if not rest.strip():
                _mask_detail_lines(lines, index + 1, building_only=True)
    return count


def mask_accounts(lines):
    """개인 계좌번호. 출금·환급 등 개인 계좌 라벨은 항상 가리고, 일반 계좌번호 라벨은 납부용 계좌만 남긴다."""
    def mask_personal(value):
        # 예금주 표기는 기관 계좌라는 근거가 아니다. 같은 줄에 이어진 다음 항목도 따로 가린다.
        account, rest = _split_at_next_field(value)
        account, masked = ACCOUNT_NUMBER.subn(ACCOUNT_MARK, account)
        rest, rest_count = _mask_other_fields(rest)
        return account + rest, masked + rest_count

    def mask_generic(value):
        account, rest = _split_at_next_field(value)
        # "예금주: 아산시"처럼 괄호 없이 이어진 예금주는 뒤 항목으로 나뉜다. 이 계좌 바로 다음 항목이
        # 예금주일 때만 함께 본다. 더 뒤에 있는 다른 계좌(가상계좌 등)의 예금주는 이 계좌와 관계없다.
        holder, _ = _split_at_next_field(rest.lstrip())
        if not re.match(r'예금주[ \t]*[:：]', holder):
            holder = ''
        rest, rest_count = _mask_other_fields(rest)
        if any(word in account for word in PAYMENT_ACCOUNT_WORDS) or AGENCY_HOLDER.search(account + ' ' + holder):
            return account + rest, rest_count
        account, masked = ACCOUNT_NUMBER.subn(ACCOUNT_MARK, account)
        return account + rest, masked + rest_count

    return (_labeled_lines(lines, PERSONAL_ACCOUNT_LABEL, mask_personal)
            + _labeled_lines(lines, GENERIC_ACCOUNT_LABEL, mask_generic))


def mask_phone_numbers(lines):
    """개인 연락처. 휴대전화 번호 전체와, 연락처 라벨이 붙은 일반 전화번호를 가린다."""
    count = 0
    for index, line in enumerate(lines):
        lines[index], masked = MOBILE_PATTERN.subn(PHONE_MARK, line)
        count += masked

    def mask_value(value):
        # 같은 줄에 이어진 다음 항목도 따로 가린다. 기관 번호는 이 항목 안에서만 판단한다.
        number, rest = _split_at_next_field(value)
        rest, rest_count = _mask_other_fields(rest)
        if _agency_line(number):
            return number + rest, rest_count
        number, masked = LANDLINE_PATTERN.subn(PHONE_MARK, number)
        return number + rest, masked + rest_count

    return count + _labeled_lines(lines, PHONE_LABEL, mask_value)


def _looks_like_name(value):
    """라벨과 같은 줄에 콜론 없이 온 값이 이름인지. 문장이나 표 머리글이면 이름이 아니다."""
    stripped = value.strip()
    if not stripped or len(stripped) > 40 or any(char.isdigit() for char in stripped):
        return False
    if stripped.split()[0] in NOT_NAME_WORDS or _SENTENCE_END.search(stripped):
        return False
    return len(stripped.split()) <= 6


def _is_department(word):
    """부서 이름에 과·팀 등이 붙은 낱말(민원팀, 이민정보과)인지."""
    match = _DEPARTMENT_WORD.match(word)
    return bool(match) and match.group('name') in _DEPARTMENT_NAMES


def _is_agency_title(word, title):
    """직위로 끝나는 낱말이 기관의 직위(아산시장, 법무부장관, 천안세무서장)인지. 아니면 이름에 직위가 붙은 것으로 본다."""
    prefix = word[:title.start()]
    found = [match.start() for match in (_AGENCY_WORD_PATTERN.search(word), _RECIPIENT_AGENCY.search(word)) if match]
    # 직위 앞부분에 기관 이름이 있다(천안세무서장, 행정복지센터장, 법무부장관).
    # 직위 자체에 든 기관 낱말(센터장의 센터, 지사장의 지사)은 세지 않는다. 홍길동센터장은 이름이다.
    if any(start < title.start() for start in found):
        return True
    # 아산시장 → 아산시, 홍성군수 → 홍성군, 충청남도지사 → 충청남도, 충남도지사 → 충남
    if prefix in _REGION_NAMES or prefix + title.group(0)[0] in _REGION_NAMES:
        return True
    # 민원팀장, 총무과장: 직위 앞부분이 부서 이름이다.
    if prefix in _DEPARTMENT_NAMES:
        return True
    # 천안지사장, 아산센터장: 직위가 기관 낱말로 시작하고 앞부분이 시·군 이름이다.
    return bool(found) and any(prefix + suffix in _REGION_NAMES for suffix in ('시', '군'))


def _mask_recipient_value(value):
    """받는 사람 라벨의 값에서 이름만 가린다. → (새 값, 가린 수, 찾은 이름 목록)

    낱말마다 직위(센터장)·기관(아산시청)·높임말(귀하)이면 남기고, 그 밖의 낱말은 이름으로 보고 가린다.
    지역 이름(_REGION_NAMES)은 뒤에 기관 이름이 이어질 때만 남긴다. 애매한 낱말은 가린다(F1-4).
    """
    if _RECIPIENT_REFERENCE.match(value):
        return value, 0, []
    words, kinds = [], []
    for word in value.split():
        title = _RECIPIENT_TITLE.search(word)
        if title and title.start() and not _is_agency_title(word, title):
            # 이름과 직위를 붙여 쓴 낱말(홍길동주무관): 이름과 직위로 나눠 이름만 가린다.
            words += [word[:title.start()], title.group(0)]
            kinds += ['name', 'keep']
            continue
        words.append(word)
        if word in _RECIPIENT_HONORIFICS:
            kinds.append('keep')
        elif title:
            kinds.append('keep')
        elif _AGENCY_WORD_PATTERN.search(word) or _RECIPIENT_AGENCY.search(word) or _is_department(word):
            kinds.append('agency')
        elif word in _REGION_NAMES:
            kinds.append('region')
        elif _TOWN_WORD.match(word):
            kinds.append('town')
        else:
            kinds.append('name')
    # 지역 낱말은 지역 낱말만 건너 바로 뒤에 기관 이름이 올 때만 남긴다(예: "아산시 배방읍 행정복지센터").
    # 읍·면은 앞 낱말이 지역 이름일 때만 지역으로 본다.
    for index in range(len(words) - 1, -1, -1):
        if kinds[index] not in ('region', 'town'):
            continue
        following = kinds[index + 1] if index + 1 < len(words) else 'name'
        after_region = index > 0 and kinds[index - 1] == 'region'
        if following in ('agency', 'keep-region') and (kinds[index] == 'region' or after_region):
            kinds[index] = 'keep-region'
        else:
            kinds[index] = 'name'

    parts, names, run = [], [], []
    for word, kind in zip(words, kinds):
        if kind == 'name':
            run.append(word)
            continue
        if run:
            names.append(' '.join(run))
            parts.append(NAME_MARK)
            run = []
        parts.append(word)
    if run:
        names.append(' '.join(run))
        parts.append(NAME_MARK)
    if not names:
        return value, 0, []
    return ' '.join(parts), len(names), names


def _name_parts(name):
    """찾은 이름에서 다른 곳에서도 가릴 부분: 전체, 괄호 앞 이름, 괄호 안 영문 이름."""
    parts = {name.strip()}
    before, _, inside = name.partition('(')
    parts.add(before.strip())
    parts.add(inside.rstrip(') \t').strip())
    # "홍길동님", "홍길동 귀하"로 적힌 이름은 높임말을 뗀 이름으로도 찾는다.
    parts.update(_ATTACHED_HONORIFIC.sub('', part) for part in tuple(parts))
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
    """값을 가린 뒤 남기는 같은 줄의 다음 항목(예: "여권번호: …")에 모든 규칙을 다시 적용한다. → (새 글자, 가린 수)

    줄 맨 앞 라벨만 보는 규칙은 줄 중간에 온 라벨을 지나치므로, 뒤 항목을 남길 때는 반드시 이 함수를 거친다.
    뒤 항목은 원래 값보다 항상 짧아 반복이 끝난다.
    """
    if not text.strip():
        return text, 0
    stripped = text.lstrip()
    lines = [stripped]
    count = sum(rule(lines) for rule in MASKING_RULES)
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
        recipient = bool(match.group('recipient'))
        # "수신 거부", "대상자 확인"처럼 받는 사람 라벨은 일반 낱말로도 흔히 쓰인다. 같은 줄의 값은
        # 콜론이 있을 때만 이름으로 본다. 라벨만 있는 줄의 다음 줄(표 형식)은 아래에서 그대로 본다.
        if recipient and value.strip() and not has_colon:
            continue
        if value.strip():
            # 같은 줄에 다른 라벨이 이어지면 이름은 그 앞에서 끝난다(예: "연락처:", "납부기한:").
            name, rest = _split_at_next_field(value)
            # 뒤에 남기는 부분도 다른 규칙으로 가린다. 줄 맨 앞 라벨만 보는 규칙(계좌·일반 전화·여권번호)은
            # "성명: 홍길동 계좌번호: …"처럼 줄 중간에 온 라벨을 앞에서 지나쳤기 때문이다.
            # 이름을 가리지 않는 경우에도 뒤 항목은 가린다.
            rest, rest_count = _mask_other_fields(rest)
            count += rest_count
            # 이름 뒤에 붙은 번호가 먼저 가려졌으면("홍길동 [전화번호 가림]") 가림 표시 앞부분만 이름으로 본다.
            # 앞부분이 비어 있으면 이름이 이미 가려진 것이다.
            name, marked = _split_at_first_mark(name)
            if not name.strip() or (not has_colon and not _looks_like_name(name)):
                lines[index] = line[:match.start('value')] + name + marked + rest
                continue
            # 가려진 번호 뒤에 괄호로 적힌 영문 이름도 이름이다. 이름을 가릴 때 함께 빼고 다른 곳에서도 가린다.
            cleaned, latin_names = _take_latin_names(marked)
            if recipient:
                # 기관·직위는 남기고 이름만 가린다. 가린 이름이 없으면(기관) 뒤의 글자도 그대로 둔다.
                new_name, masked, names = _mask_recipient_value(name)
                lines[index] = line[:match.start('value')] + new_name + (cleaned if masked else marked) + rest
                found.extend(names + latin_names if masked else names)
                count += masked
                continue
            found.extend([name, *latin_names])
            lines[index] = match.group('label') + match.group('sep') + NAME_MARK + cleaned + rest
            count += 1
            continue
        following = _next_value_index(lines, index)
        if following is None:
            continue
        # 같은 줄에 다른 항목이 이어지면("홍길동 연락처: …") 이름은 그 앞에서 끝난다. 같은 줄의 값과 같은 기준이다.
        value, rest = _split_at_next_field(lines[following])
        # 이름 뒤에 붙은 번호가 먼저 가려졌으면 가림 표시 앞부분만 이름으로 본다.
        candidate, marked = _split_at_first_mark(value)
        # 앞부분이 비었으면 이미 가려진 것이고, 숫자가 있으면 이름이 아니다. 표의 값 칸이라 문장 끝 검사는 하지 않는다.
        if not candidate.strip() or any(char.isdigit() for char in candidate):
            continue
        # 뒤에 남기는 항목도 다른 규칙으로 가린다.
        rest, rest_count = _mask_other_fields(rest)
        count += rest_count
        cleaned, latin_names = _take_latin_names(marked)
        cleaned += rest
        if recipient:
            new_line, masked, names = _mask_recipient_value(candidate)
            if masked:
                lines[following] = new_line + cleaned
                found.extend(names + latin_names)
                count += masked
            elif rest_count:
                # 받는 사람이 기관이라 이름은 그대로 두고, 뒤 항목에서 가린 것만 반영한다.
                lines[following] = candidate + marked + rest
            continue
        found.extend([candidate, *latin_names])
        lines[following] = NAME_MARK + cleaned
        count += 1

    # 뒤 항목 안에서 찾은 이름도 문서 전체에서 가릴 수 있게 mask_text()로 넘긴다.
    collected = _found_names.get()
    if collected is not None:
        collected.extend(found)
    return count + _mask_repeated_names(lines, found)


def _mask_repeated_names(lines, names):
    """라벨로 확인한 이름이 다른 곳에 다시 나오면 가린다. 가린 수를 돌려준다."""
    count = 0
    parts = sorted({part for name in names for part in _name_parts(name)}, key=len, reverse=True)
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
    names = []
    token = _found_names.set(names)
    try:
        count = sum(rule(lines) for rule in MASKING_RULES)
    finally:
        _found_names.reset(token)
    # 줄 중간의 "성명:"처럼 뒤 항목에서 찾은 이름은 그 줄 안에서만 가려졌다. 문서 전체에 한 번 더 적용한다.
    count += _mask_repeated_names(lines, names)
    return {'masked_text': '\n'.join(lines), 'masked_count': count}
