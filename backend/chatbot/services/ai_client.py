"""로컬 더미 AI 경계. 외부 전송은 구현하지 않으며 실패를 더미 성공으로 바꾸지 않는다."""

import re
from copy import deepcopy

from django.conf import settings

from documents.services.pipeline import mask_personal_info

MODE = 'dummy'
DUMMY_ANSWERS = {
    'ko': '현재 공식 근거를 확인할 수 없습니다. 정확한 내용은 담당기관에 확인해 주세요.',
    'vi': 'Hiện chưa thể xác minh nguồn chính thức. Vui lòng liên hệ cơ quan phụ trách để xác nhận thông tin chính xác.',
    'en': 'Official sources cannot currently be verified. Please confirm the details with the responsible agency.',
    'zh': '目前无法核实官方依据。请向主管机关确认准确信息。',
    'uz': 'Hozircha rasmiy manbalarni tasdiqlab bo‘lmaydi. Aniq ma’lumotni mas’ul idoradan tekshiring.',
    'ne': 'हाल आधिकारिक स्रोत पुष्टि गर्न सकिँदैन। सही जानकारीका लागि सम्बन्धित निकायमा सम्पर्क गर्नुहोस्।',
    'th': 'ขณะนี้ยังไม่สามารถตรวจสอบแหล่งข้อมูลทางการได้ โปรดติดต่อหน่วยงานที่รับผิดชอบเพื่อยืนยันข้อมูลที่ถูกต้อง',
}
PHONE_PATTERN = re.compile(r'(?<!\d)(?:\+82[- ]?|0)\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)')


class AIClientError(Exception):
    """호출 오류·시간 초과·응답 계약 오류. 외부 오류 문자열을 사용자에게 전달하지 않는다."""


def prepare_ai_payload(payload):
    """질문·이력·해석의 기본 마스킹 경계. 외부 전송용 완전한 비식별화를 보증하지 않는다.

    실제 외부 어댑터 도입 전 주소/자유문 등 개인정보 정책 검토와 전송 제한이 필요하다.
    파일 객체나 OCR 원문을 받지 않으며 현재 dummy 외의 모드는 fail closed다.
    """
    if isinstance(payload, str):
        return PHONE_PATTERN.sub('[전화번호 가림]', mask_personal_info(payload)['masked_text'])
    if isinstance(payload, list):
        return [prepare_ai_payload(value) for value in payload]
    if isinstance(payload, dict):
        return {key: prepare_ai_payload(value) for key, value in payload.items()}
    return deepcopy(payload)


def generate_answer(question, interpretation, history, language, official_sources):
    """이 함수만 실제 AI 어댑터로 교체한다. 입력과 출력 계약은 README 참고.

    실제 어댑터는 제한된 연결/읽기 timeout을 적용하고 오류 시 AIClientError를 발생시킨다.
    dummy 모드는 네트워크 요청/SDK/자격증명을 사용하지 않는다.

    rag 모드: backend/ai/chatbot RAG 모듈(ai.chatbot.integration.chat_answer)을 호출한다.
    timeout은 AI_REQUEST_TIMEOUT(기본 120초)을 연결/읽기에 적용한다. 출처는 official_sources 안의
    sourceDocumentId만 내며, 그 밖의 자료만 근거로 쓴 답변은 UNVERIFIED로 낸다.
    """
    mode = getattr(settings, 'CHATBOT_AI_MODE', MODE)
    if mode == 'rag':
        try:
            from ai.chatbot.integration import chat_answer

            return chat_answer(question, interpretation, history, language, official_sources)
        except Exception:
            # 외부 오류 문자열에 질문·문서가 섞일 수 있으므로 원문을 버린다.
            raise AIClientError() from None
    if mode != 'dummy':
        raise AIClientError()
    if language not in DUMMY_ANSWERS:
        raise AIClientError()
    return {
        'answer': DUMMY_ANSWERS[language], 'language': language,
        'sources': [], 'grounded': False, 'status': 'UNVERIFIED',
        'suggestedQuestions': [],
    }
