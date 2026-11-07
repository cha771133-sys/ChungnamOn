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

# 숫자 6자리와 7자리. 공백으로 쪼개지거나 하이픈이 없어도 찾는다.
# 하이픈 없는 13자리 번호도 함께 가려지지만, 누락보다 과잉 마스킹을 택한다(F1-4).
# 줄바꿈은 건너뛰지 않도록 공백을 [ \t]로 제한한다.
REGISTRATION_NUMBER_PATTERN = re.compile(
    r'(?<!\d)(\d(?:[ \t]?\d){5})[ \t]*[-‐–—−]?[ \t]*(\d(?:[ \t]?\d){6})(?!\d)'
)


class OCRError(Exception):
    """OCR 실패. 메시지에 OCR 원문, Secret Key, Invoke URL을 넣지 않는다."""


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
    return lines


def _line_text(line):
    """한 줄의 단어들을 공백으로 이어 글자로 만든다."""
    return ' '.join(field.get('inferText', '') for field in line)


def normalize_registration_numbers(text):
    """쪼개져 인식된 외국인등록번호를 마스킹 정규식이 찾는 ######-####### 형태로 잇는다."""
    def join(match):
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
    """기한 라벨이 있는 줄의 날짜를 찾는다. 그 줄에 날짜가 없으면 다음 줄에서 찾는다."""
    found = []
    for image in images:
        lines = _split_lines(image.get('fields', []))
        for index, line in enumerate(lines):
            if not any(label in _line_text(line) for label in DEADLINE_LABELS):
                continue
            dates = [field for field in line if _is_date(field)]
            if not dates and index + 1 < len(lines):
                dates = [field for field in lines[index + 1] if _is_date(field)]
            found.extend(dates)
    return found


def calculate_confidence(images):
    """핵심 항목(기한·금액·기관명) 단어의 신뢰도 중 최솟값. 하나도 없으면 0.0.

    최솟값을 쓰는 이유: 평균을 내면 기한 단어 하나를 잘못 읽어도 다른 단어에 묻힌다.
    기한 라벨을 찾지 못한 문서는 문서 안의 모든 날짜를 기한 후보로 본다.
    찾지 못한 분류는 빼고 찾은 분류만으로 계산한다.
    """
    fields = [field for image in images for field in image.get('fields', [])]
    dates = _deadline_fields(images) or [field for field in fields if _is_date(field)]
    amounts = [field for field in fields if MONEY_PATTERN.search(field.get('inferText', ''))]
    agencies = [
        field for field in fields
        if any(word in field.get('inferText', '') for word in AGENCY_WORDS)
    ]
    key_fields = dates + amounts + agencies
    if not key_fields:
        return 0.0
    return min(float(field.get('inferConfidence', 0.0)) for field in key_fields)


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
