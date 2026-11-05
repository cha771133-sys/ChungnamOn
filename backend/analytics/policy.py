"""이벤트에 저장할 수 있는 코드·숫자만 정의한다. 자유문과 요청 본문은 받지 않는다."""

from django.core.exceptions import ValidationError

STAGES = {'QUEUED', 'OCR', 'MASKING', 'INTERPRETING', 'VERIFYING', 'DONE'}
CHAT_STATUSES = {'GROUNDED', 'UNVERIFIED', 'MISMATCH'}
PROPERTY_RULES = {
    'DOCUMENT_ANALYSIS_STARTED': {},
    'DOCUMENT_INTERPRETATION_COMPLETED': {'has_translation': bool, 'has_summary': bool},
    'DOCUMENT_INTERPRETATION_FAILED': {'stage': STAGES},
    'DOCUMENT_RESULT_PROVIDED': {},
    'CHAT_ANSWER_CREATED': {'status': CHAT_STATUSES, 'grounded': bool, 'official_source_count': int},
}


def validate_properties(event_type, properties):
    rules = PROPERTY_RULES.get(event_type)
    if rules is None or not isinstance(properties, dict) or set(properties) != set(rules):
        raise ValidationError('이벤트 종류별 허용 속성만 빠짐없이 전달해야 합니다.')
    for name, rule in rules.items():
        value = properties[name]
        if isinstance(rule, set):
            valid = isinstance(value, str) and value in rule
        else:
            valid = type(value) is rule
            if rule is int:
                valid = valid and 0 <= value <= 20
        if not valid:
            raise ValidationError('이벤트 속성은 허용된 상태 코드·논리값·개수여야 합니다.')
    if event_type == 'CHAT_ANSWER_CREATED':
        if properties['grounded'] != (properties['status'] == 'GROUNDED'):
            raise ValidationError('챗봇 근거 상태가 일치하지 않습니다.')
        if properties['grounded'] and not properties['official_source_count']:
            raise ValidationError('공식 근거 연결이 필요합니다.')
