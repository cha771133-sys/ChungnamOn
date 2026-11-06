"""문서 처리 파이프라인.

OCR → 마스킹 → 해석 → 검증 순서로 실행하고 단계마다 Document.status·step을 갱신한다.
run_ocr, mask_personal_info, interpret, verify는 AI 모듈 자리의 더미 함수다.
AI 모듈이 준비되면 docstring의 입출력 형식을 지키면서 함수 내용만 교체한다.

설정값 (settings에 없으면 기본값 사용)
- DOCUMENTS_STEP_DELAY: 단계마다 넣는 지연(초). 기본 1.0, 테스트에서는 0
- DOCUMENTS_LOW_CONFIDENCE_THRESHOLD: OCR 신뢰도가 이 값 미만이면 low_confidence. 기본 0.7
"""

import logging
import re
import time

from django.conf import settings
from django.db import transaction

from accounts.models import UserProfile

from ..models import Document, InterpretationResult

logger = logging.getLogger(__name__)

# 프로필이 없거나 안내 언어를 고르지 않은 사용자에게 쓰는 기본 언어
DEFAULT_LANGUAGE = 'vi'

FAIL_MESSAGES = {
    Document.Step.OCR: '문서의 글자를 읽지 못했습니다. 밝은 곳에서 다시 촬영해 업로드해 주세요.',
    Document.Step.MASKING: '개인정보 가리기에 실패해 결과를 보여드릴 수 없습니다. 다시 업로드해 주세요.',
    Document.Step.INTERPRETING: '문서 해석 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
    Document.Step.VERIFYING: '공식 자료 확인 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.',
}
DEFAULT_FAIL_MESSAGE = '문서 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'

# 처리를 (다시) 시작할 수 있는 상태. PROCESSING·DONE이면 시작하지 않는다.
STARTABLE_STATUSES = [Document.Status.PENDING, Document.Status.FAILED]

DUMMY_OCR_TEXT = """2026년도 주민세(개인분) 납부 안내
납세자: 응우옌 반 A
외국인등록번호: 900101-5123456
주소: 충청남도 아산시 배방읍 00로 00, 000동 000호
세액: 6,250원
납부기한: 2026년 8월 31일
납부 방법: 은행 창구, 가상계좌, 위택스(www.wetax.go.kr)
문의: 아산시청 세무과 041-000-0000"""

REGISTRATION_NUMBER_PATTERN = re.compile(r'\d{6}-\d{7}')
ADDRESS_PATTERN = re.compile(r'(주소\s*[:：]\s*)[^\n]+')


def run_ocr(document):
    """문서 파일에서 글자를 읽는다. (더미)

    입력: document (Document) — document.file, document.file_type 사용
    출력: {
        "text": str,          # 인식한 원문 전체
        "confidence": float,  # 0~1, 핵심 항목(기한·금액·기관명) 기준 신뢰도
    }
    """
    return {'text': DUMMY_OCR_TEXT, 'confidence': 0.92}


def mask_personal_info(text):
    """원문에서 외국인등록번호·주소 같은 개인정보를 가린다. (더미: 정규식)

    입력: text (str) — OCR 원문
    출력: {
        "masked_text": str,   # 개인정보를 가린 원문
        "masked_count": int,  # 가린 항목 수
    }
    마스킹에 실패하면 예외를 던진다. 실패한 문서는 결과를 내보내지 않는다(F1-4).
    """
    masked_text, registration_count = REGISTRATION_NUMBER_PATTERN.subn('******-*******', text)
    masked_text, address_count = ADDRESS_PATTERN.subn(r'\1[주소 가림]', masked_text)
    return {'masked_text': masked_text, 'masked_count': registration_count + address_count}


def interpret(masked_text, language):
    """마스킹된 원문을 사용자 언어로 해석한다. (더미: 아산시 주민세 고지서)

    입력: masked_text (str), language (str, 언어 코드 예: "vi")
    출력: {
        "doc_type": str,                    # 문서 유형 예: "고지서"
        "title": str,
        "original_text": str,               # 마스킹된 원문 그대로
        "translated_text": str,             # language로 번역한 전문
        "language": str,                    # translated_text의 실제 언어 코드. 결과에 이 값을 저장한다
        "summary": [str],                   # 핵심 요약 문장
        "terms": [{"word": str, "explain": str}],
        "deadlines": [{"label": str, "date": "YYYY-MM-DD"}],
        "required_docs": [str],
        "agency": {"name": str, "phone": str, "source": "document" | "official" | "unknown"},
    }
    """
    # 더미는 요청 언어와 상관없이 베트남어 번역만 돌려준다.
    return {
        'doc_type': '고지서',
        'title': '주민세 납부 안내',
        'original_text': masked_text,
        'language': 'vi',
        'translated_text': (
            'Thông báo nộp thuế cư trú (cá nhân) năm 2026\n'
            'Số tiền thuế: 6.250 won\n'
            'Hạn nộp: ngày 31 tháng 8 năm 2026\n'
            'Cách nộp: tại quầy ngân hàng, tài khoản ảo, Wetax (www.wetax.go.kr)\n'
            'Liên hệ: Phòng Thuế, Tòa thị chính Asan'
        ),
        'summary': [
            '2026년 8월 31일까지 주민세 6,250원을 내야 합니다.',
            '은행 창구, 가상계좌, 위택스에서 낼 수 있습니다.',
        ],
        'terms': [
            {'word': '주민세(개인분)', 'explain': '그 지역에 사는 사람이 1년에 한 번 내는 세금'},
            {'word': '납부기한', 'explain': '돈을 내야 하는 마지막 날'},
            {'word': '가상계좌', 'explain': '이 고지서의 세금만 받는 전용 은행 계좌'},
        ],
        'deadlines': [{'label': '납부 마감', 'date': '2026-08-31'}],
        'required_docs': ['고지서', '외국인등록증'],
        'agency': {'name': '아산시청 세무과', 'phone': '041-000-0000', 'source': 'document'},
    }


def verify(result):
    """해석 결과를 공식 자료와 대조한다. (더미)

    입력: result (dict) — interpret()의 출력
    출력: {
        "status": "MATCHED" | "MISMATCH" | "UNVERIFIED",
        "sources": [{"title": str, "url": str, "checkedAt": "YYYY-MM-DD"}],
    }
    발신기관·지역을 식별하지 못하면 "UNVERIFIED"를 반환한다(F2-1).
    """
    return {
        'status': 'MATCHED',
        'sources': [
            {
                'title': '아산시 지방세 납부 안내 (더미)',
                'url': 'https://www.asan.go.kr/',
                'checkedAt': '2026-10-30',
            },
        ],
    }


def get_language(document):
    language = UserProfile.objects.filter(user_id=document.owner_id).values_list(
        'language', flat=True,
    ).first()
    return language or DEFAULT_LANGUAGE


def process_document(document_id):
    """문서 하나를 처리한다. 처리를 시작하지 않았으면 False를 반환한다."""
    # 조건부 update로 상태를 선점해 같은 문서가 동시에 두 번 처리되지 않게 한다.
    claimed = Document.objects.filter(id=document_id, status__in=STARTABLE_STATUSES).update(
        status=Document.Status.PROCESSING,
        step=Document.Step.QUEUED,
        fail_reason='',
    )
    if not claimed:
        return False

    document = Document.objects.select_related('owner').get(id=document_id)
    try:
        _advance(document, Document.Step.OCR)
        ocr = run_ocr(document)

        _advance(document, Document.Step.MASKING)
        masked = mask_personal_info(ocr['text'])

        _advance(document, Document.Step.INTERPRETING)
        result = interpret(masked['masked_text'], get_language(document))

        _advance(document, Document.Step.VERIFYING)
        verification = verify(result)

        _save_result(document, ocr, masked, result, verification)
    except Exception:
        logger.exception('문서 처리 실패: document_id=%s step=%s', document.id, document.step)
        document.status = Document.Status.FAILED
        document.fail_reason = FAIL_MESSAGES.get(document.step, DEFAULT_FAIL_MESSAGE)
        document.save(update_fields=['status', 'fail_reason'])
    return True


def _advance(document, step):
    document.step = step
    document.save(update_fields=['step'])
    delay = getattr(settings, 'DOCUMENTS_STEP_DELAY', 1.0)
    if delay:
        time.sleep(delay)


def _save_result(document, ocr, masked, result, verification):
    threshold = getattr(settings, 'DOCUMENTS_LOW_CONFIDENCE_THRESHOLD', 0.7)
    with transaction.atomic():
        InterpretationResult.objects.update_or_create(
            document=document,
            defaults={
                'doc_type': result['doc_type'],
                'title': result['title'],
                'original_text': result['original_text'],
                'translated_text': result['translated_text'],
                'summary': result['summary'],
                'terms': result['terms'],
                'deadlines': result['deadlines'],
                'required_docs': result['required_docs'],
                'agency': result['agency'],
                'verification': verification,
                'language': result['language'],
            },
        )
        document.doc_type = result['doc_type']
        document.title = result['title']
        document.masked_count = masked['masked_count']
        document.low_confidence = ocr['confidence'] < threshold
        document.status = Document.Status.DONE
        document.step = Document.Step.DONE
        document.save(update_fields=[
            'doc_type', 'title', 'masked_count', 'low_confidence', 'status', 'step',
        ])
