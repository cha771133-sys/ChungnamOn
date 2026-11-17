"""CLOVA OCR General 호출과 응답 변환.

요청·응답 형식: https://api.ncloud-docs.com/docs/ai-application-service-ocr-ocr
pipeline.run_ocr()가 OCR_MODE=clova일 때 run_clova_ocr()를 호출한다.

OCR 원문과 Secret Key, Invoke URL은 로그에 남기지 않는다. pipeline이 예외를 traceback까지
기록하므로 예외 메시지에도 넣지 않고, 원래 예외는 `from None`으로 숨긴다.
"""

import base64
import math
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
from django.conf import settings

from ..models import Document
from .masking import is_labeled_other_number

CLOVA_VERSION = 'V2'
CLOVA_LANG = 'ko'
# 업로드 파일명에 개인정보가 있을 수 있어 CLOVA에는 고정된 이름만 보낸다.
CLOVA_IMAGE_NAME = 'document'
# (연결, 읽기) 초. PDF는 페이지가 많을수록 오래 걸려 읽기 시간을 넉넉히 둔다.
CLOVA_TIMEOUT = (5, 60)
SUPPORTED_FORMATS = {'jpg', 'jpeg', 'png', 'pdf'}

# 신뢰도 계산에 쓰는 단어 목록. 새 문서 유형이 정해지면 여기에 추가한다.
DEADLINE_LABELS = ('기한', '마감', '까지')
AGENCY_WORDS = ('시청', '군청', '구청', '도청', '세무서', '공단', '지사', '사무소', '센터', '세무과', '출입국')

DATE_PATTERN = re.compile(r'\d{4}년|\d{1,2}월|\d{1,2}일|\d{4}[./-]\d{1,2}[./-]\d{1,2}')
MONEY_PATTERN = re.compile(r'\d[\d,]*원')
# 기한 이름표로 끝나는 줄(예: "납부기한", "신청 기한:"). 이런 줄만 날짜가 다음 줄에 온다.
# "기한 내에 … 부과될 수 있습니다." 같은 문장의 다음 줄 날짜(발행일 등)는 기한이 아니다.
# "마감 일"처럼 띄어 읽힌 경우도 이름표로 본다.
DEADLINE_LABEL_LINE_PATTERN = re.compile(r'(기한|마감(\s*일)?)\W*$')
# 법 이름(예: 「출입국관리법」, 출입국관리법에)은 기관 낱말이 들어 있어도 기관명이 아니다.
# 조사가 붙어 한 단어로 읽힌 경우도 뺀다. 법무부처럼 '법'이 낱말 중간에 있는 기관명은 남는다.
LAW_NAME_PATTERN = re.compile(
    r'(법|시행령|시행규칙)(에서|에는|에도|에|으로|로|은|는|이|가|의|을|를|과|와|도|상)?(\W|$)'
)
# CLOVA가 PDF 페이지 수 제한(10쪽)을 넘은 요청을 거부할 때 응답 본문에 넣는 code 값.
CLOVA_PAGE_LIMIT_CODE = '0011'
# 기울여 찍은 사진의 줄 순서를 좌표로 다시 맞추는 기준. 단어 상자의 기울기가 이 각도(도) 이상일 때만 한다.
# 실제로 재 본 값: 0도(견본 PDF·PNG, 정면 사진)는 순서가 맞고, 10.5도부터 어긋나기 시작해 14.9도에서는
# "입국일"과 날짜가 떨어지고 29.1도에서는 "주소"와 주소 값이 떨어졌다. 정면 사진도 단어 하나하나는 ±5도쯤
# 흔들리지만 전체의 중앙값은 0도로 나온다.
SKEW_REORDER_DEGREES = 5.0
# 기울기를 잴 때 필요한 단어 상자의 최소 개수. 상자가 너무 적으면 한두 개의 오차에 각도가 좌우된다.
SKEW_MIN_BOXES = 5
# 세로 위치의 차이가 글자 높이의 이 배수 안이면 같은 행으로 본다.
SKEW_ROW_GAP_RATIO = 0.5

# 숫자 6자리와 7자리. 공백으로 쪼개지거나 하이픈이 없어도 찾는다.
# 하이픈 없는 13자리 숫자도 등록번호로 본다(누락보다 과잉 마스킹, F1-4). 다만 고지번호처럼 이름표로 보아
# 다른 번호가 분명하면 normalize_registration_numbers()가 바꾸지 않는다(masking.is_labeled_other_number).
# 줄바꿈은 건너뛰지 않도록 공백을 [ \t]로 제한한다.
REGISTRATION_NUMBER_PATTERN = re.compile(
    r'(?<!\d)(\d(?:[ \t]?\d){5})[ \t]*[-‐–—−]?[ \t]*(\d(?:[ \t]?\d){6})(?!\d)'
)


class OCRError(Exception):
    """OCR 실패. 메시지에 OCR 원문, Secret Key, Invoke URL을 넣지 않는다."""


class PageLimitOCRError(OCRError):
    """PDF 페이지가 너무 많아 CLOVA가 요청 전체를 거부했다.

    다시 촬영해도 해결되지 않는 실패라 다른 OCR 실패와 구분한다.
    OCRError의 하위 클래스라서 구분하지 않는 쪽에서는 기존과 똑같이 처리된다.
    """


def _error_code(response):
    """오류 응답 본문의 code 값. 읽을 수 없으면 None. 본문의 다른 값(path 등)은 쓰지 않는다."""
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    code = data.get('code')
    return code if isinstance(code, str) else None


def _validate_images(images):
    """images 항목·fields·inferConfidence 형식과 인식 결과를 확인한다."""
    invalid = OCRError('CLOVA 응답 형식이 올바르지 않습니다.')
    if not isinstance(images, list) or not images:
        raise invalid
    for image in images:
        if not isinstance(image, dict):
            raise invalid
        # 일부 페이지만 실패해도 기한·금액을 놓칠 수 있어 문서 전체를 실패로 본다.
        # 응답 값은 문서 내용일 수 있어 오류 메시지에 넣지 않는다.
        if image.get('inferResult') != 'SUCCESS':
            raise OCRError('CLOVA 인식에 실패했습니다.')
        fields = image.get('fields', [])
        if not isinstance(fields, list):
            raise invalid
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get('inferText'), str):
                raise invalid
            confidence = field.get('inferConfidence')
            # bool은 int의 하위 타입이고, NaN은 비교가 항상 거짓이라 저신뢰 판정을 끌 수 있다.
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
                raise invalid


def call_clova(file_bytes, file_format):
    """파일을 CLOVA로 보내고 응답의 images 목록을 돌려준다."""
    invoke_url = getattr(settings, 'CLOVA_OCR_INVOKE_URL', '')
    secret = getattr(settings, 'CLOVA_OCR_SECRET', '')
    if not invoke_url or not secret:
        raise OCRError('CLOVA OCR 설정이 없습니다.')
    # http로 보내면 원본 문서와 Secret Key가 암호화되지 않은 채 전송된다.
    if urlsplit(invoke_url).scheme.lower() != 'https':
        raise OCRError('CLOVA OCR 주소는 HTTPS여야 합니다.')

    body = {
        'version': CLOVA_VERSION,
        'requestId': str(uuid.uuid4()),
        'timestamp': int(time.time() * 1000),
        'lang': CLOVA_LANG,
        'images': [{
            'format': file_format,
            'name': CLOVA_IMAGE_NAME,
            'data': base64.b64encode(file_bytes).decode('ascii'),
        }],
    }
    headers = {'Content-Type': 'application/json', 'X-OCR-SECRET': secret}

    try:
        # 리디렉션을 따라가면 다른 서버로 Secret Key와 문서가 전송될 수 있다.
        response = requests.post(
            invoke_url, headers=headers, json=body, timeout=CLOVA_TIMEOUT, allow_redirects=False,
        )
    except requests.RequestException as error:
        # requests의 오류 문구에는 Invoke URL이 들어갈 수 있어 오류 종류만 남긴다.
        raise OCRError(f'CLOVA 호출 실패: {type(error).__name__}') from None

    # response.ok는 3xx도 성공으로 보므로 리디렉션을 오류로 처리하도록 2xx를 직접 확인한다.
    if not 200 <= response.status_code < 300:
        if _error_code(response) == CLOVA_PAGE_LIMIT_CODE:
            raise PageLimitOCRError('PDF 페이지 수가 CLOVA의 제한을 넘었습니다.')
        raise OCRError(f'CLOVA 오류 응답: HTTP {response.status_code}')

    try:
        images = response.json()['images']
    except (ValueError, KeyError, TypeError):
        raise OCRError('CLOVA 응답 형식이 올바르지 않습니다.') from None
    _validate_images(images)
    return images


def _split_lines(fields):
    """lineBreak가 true인 단어까지를 한 줄로 묶는다. 줄은 단어(field) 목록이다."""
    lines, line = [], []
    for field in fields:
        line.append(field)
        if field.get('lineBreak'):
            lines.append(line)
            line = []
    if line:
        lines.append(line)
    return _reorder_skewed_lines(lines)


def _box(field):
    """단어 상자의 네 꼭짓점 [(x, y), …]. 좌표가 없거나 모양이 다르면 None."""
    poly = field.get('boundingPoly')
    vertices = poly.get('vertices') if isinstance(poly, dict) else None
    if not isinstance(vertices, list) or len(vertices) != 4:
        return None
    points = []
    for vertex in vertices:
        if not isinstance(vertex, dict):
            return None
        x, y = vertex.get('x'), vertex.get('y')
        for value in (x, y):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return None
        points.append((float(x), float(y)))
    return points


def _skew_degrees(boxes):
    """단어 상자 윗변들의 기울기(도). 한 장이 통째로 돌아간 각도를 본다. 잴 수 없으면 None.

    짧은 상자는 각도가 부정확하므로 길이로 무게를 둔 평균 방향을 먼저 구하고, 그 방향을 기준으로 한 중앙값을 쓴다.
    평균 방향을 기준으로 삼는 것은 180도 근처(뒤집힌 사진)에서 각도가 -180과 180으로 갈리지 않게 하려는 것이다.
    """
    edges = [(x1 - x0, y1 - y0) for (x0, y0), (x1, y1), _, _ in boxes]
    edges = [(dx, dy) for dx, dy in edges if math.hypot(dx, dy) >= 1]
    if len(edges) < SKEW_MIN_BOXES:
        return None
    mean = math.degrees(math.atan2(sum(dy for _, dy in edges), sum(dx for dx, _ in edges)))
    offsets = sorted((math.degrees(math.atan2(dy, dx)) - mean + 180) % 360 - 180 for dx, dy in edges)
    middle = len(offsets) // 2
    median = offsets[middle] if len(offsets) % 2 else (offsets[middle - 1] + offsets[middle]) / 2
    return mean + median


def _reorder_skewed_lines(lines):
    """기울여 찍은 사진에서 줄 순서를 좌표로 다시 맞춘다. 줄 안의 단어 묶음은 그대로 두고 순서만 바꾼다.

    사진이 돌아가 있으면 CLOVA가 주는 줄 순서가 어긋나 이름표와 값이 떨어진다(10도쯤부터 시작해 30도에서는
    "주소"와 주소 값 사이에 다른 줄들이 낀다). 그러면 마스킹과 기한 계산이 값을 찾지 못한다.
    기울기가 SKEW_REORDER_DEGREES 미만이거나 좌표 없는 단어가 하나라도 있으면 CLOVA가 준 순서를 그대로 쓴다.
    """
    boxed = [[_box(field) for field in line] for line in lines]
    # 좌표가 없거나 모양이 다른 단어가 하나라도 있으면 그 줄을 어디에 둘지 믿을 수 없다. 그대로 둔다.
    if len(lines) < 2 or not all(box for boxes in boxed for box in boxes):
        return lines
    angle = _skew_degrees([box for boxes in boxed for box in boxes])
    if angle is None or abs(angle) < SKEW_REORDER_DEGREES:
        return lines

    # 기울기를 되돌린 좌표에서 줄마다 세로 위치(단어 중심의 평균), 가로 위치(가장 왼쪽), 글자 높이를 구한다.
    cos, sin = math.cos(math.radians(-angle)), math.sin(math.radians(-angle))
    placed, heights = [], []
    for line, boxes in zip(lines, boxed):
        centers = [(sum(x for x, _ in box) / 4, sum(y for _, y in box) / 4) for box in boxes]
        rotated = [(x * cos - y * sin, x * sin + y * cos) for x, y in centers]
        placed.append((sum(y for _, y in rotated) / len(rotated), min(x for x, _ in rotated), line))
        heights.extend(math.hypot(box[3][0] - box[0][0], box[3][1] - box[0][1]) for box in boxes)
    heights.sort()
    row_gap = heights[len(heights) // 2] * SKEW_ROW_GAP_RATIO

    # 위에서 아래로 훑으며 세로 위치가 가까운 줄을 같은 행으로 묶고, 행 안에서는 왼쪽부터 놓는다.
    placed.sort(key=lambda item: item[0])
    rows, row = [], []
    for item in placed:
        if row and item[0] - sum(other[0] for other in row) / len(row) > row_gap:
            rows.append(row)
            row = []
        row.append(item)
    rows.append(row)
    return [line for row in rows for _, _, line in sorted(row, key=lambda item: item[1])]


def _line_text(line):
    """한 줄의 단어들을 공백으로 이어 글자로 만든다."""
    return ' '.join(field.get('inferText', '') for field in line)


def normalize_registration_numbers(text):
    """쪼개져 인식된 외국인등록번호를 마스킹 정규식이 찾는 ######-####### 형태로 잇는다.

    이름표로 보아 등록번호가 아닌 번호(고지번호 등)는 바꾸지 않는다. 기준은 masking.py와 같다.
    """
    def join(match):
        if is_labeled_other_number(text, match.start(), match.group(0)):
            return match.group(0)
        front = re.sub(r'[ \t]', '', match.group(1))
        back = re.sub(r'[ \t]', '', match.group(2))
        return f'{front}-{back}'

    return REGISTRATION_NUMBER_PATTERN.sub(join, text)


def build_text(images):
    """응답을 원문 줄 구조의 텍스트로 만든다. 페이지 사이도 줄바꿈으로 잇는다."""
    lines = [
        _line_text(line)
        for image in images
        for line in _split_lines(image.get('fields', []))
    ]
    return normalize_registration_numbers('\n'.join(lines))


def _is_date(field):
    return bool(DATE_PATTERN.search(field.get('inferText', '')))


def _deadline_fields(images):
    """기한 라벨이 있는 줄의 날짜를 찾는다.

    그 줄에 날짜가 없으면, 줄이 기한 이름표로 끝날 때만 다음 줄에서 찾는다.
    """
    found = []
    for image in images:
        lines = _split_lines(image.get('fields', []))
        for index, line in enumerate(lines):
            text = _line_text(line)
            if not any(label in text for label in DEADLINE_LABELS):
                continue
            dates = [field for field in line if _is_date(field)]
            if not dates and index + 1 < len(lines) and DEADLINE_LABEL_LINE_PATTERN.search(text):
                dates = [field for field in lines[index + 1] if _is_date(field)]
            found.extend(dates)
    return found


def _is_agency(field):
    text = field.get('inferText', '')
    return any(word in text for word in AGENCY_WORDS) and not LAW_NAME_PATTERN.search(text)


def calculate_confidence(images):
    """핵심 항목(기한·금액·기관명)을 믿을 수 있는 정도. 하나도 없으면 0.0.

    기한 날짜와 금액은 단어 신뢰도의 최솟값을 쓴다. 평균을 내면 기한 단어 하나를
    잘못 읽어도 다른 단어에 묻힌다.
    기관명은 가장 잘 읽힌 단어 하나의 신뢰도를 쓴다. 기관명은 문서에 여러 번 나오고,
    가운뎃점·괄호가 붙은 긴 낱말은 맞게 읽혀도 신뢰도가 낮게 나온다(깨끗한 견본에서
    0.42). 모든 기관 단어의 최솟값을 쓰면 깨끗한 문서가 낮게 나온다.
    결과는 이 값들 중 최솟값이다.

    기한 라벨을 찾지 못한 문서는 문서 안의 모든 날짜를 기한 후보로 본다.
    찾지 못한 분류는 빼고 찾은 분류만으로 계산한다.
    """
    fields = [field for image in images for field in image.get('fields', [])]
    dates = _deadline_fields(images) or [field for field in fields if _is_date(field)]
    amounts = [field for field in fields if MONEY_PATTERN.search(field.get('inferText', ''))]
    agencies = [field for field in fields if _is_agency(field)]

    values = [float(field.get('inferConfidence', 0.0)) for field in dates + amounts]
    if agencies:
        values.append(max(float(field.get('inferConfidence', 0.0)) for field in agencies))
    if not values:
        return 0.0
    return min(values)


def _file_format(document):
    """CLOVA 요청의 format 값을 정한다."""
    if document.file_type == Document.FileType.PDF:
        return 'pdf'
    # IMAGE는 jpg와 png를 구분하지 않아 확장자로 판단한다.
    file_format = Path(document.file.name).suffix.lower().lstrip('.')
    if file_format not in SUPPORTED_FORMATS:
        raise OCRError('지원하지 않는 파일 형식입니다.')
    return file_format


def _read_file(document):
    """문서 파일을 읽는다. 읽지 못하면 경로를 숨긴 OCRError를 던진다."""
    try:
        with document.file.open('rb') as file:
            return file.read()
    except OSError:
        # 오류 문구의 파일 경로에 개인정보가 있을 수 있어 숨긴다.
        raise OCRError('문서 파일을 읽을 수 없습니다.') from None


def run_clova_ocr(document):
    """문서를 CLOVA로 읽어 pipeline.run_ocr() 계약 형식으로 돌려준다. 실패하면 OCRError."""
    images = call_clova(_read_file(document), _file_format(document))
    text = build_text(images)
    if not text.strip():
        raise OCRError('인식한 글자가 없습니다.')
    return {'text': text, 'confidence': calculate_confidence(images)}
