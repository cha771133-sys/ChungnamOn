"""interpret이 낸 checklist를 저장 가능한 값으로 정리한다.

형식이 틀려도 문서 처리를 실패시키지 않도록, 고칠 수 있는 값은 고치고 쓸 수 없는 항목은 뺀다.
"""

import re
from datetime import date

from ..models import ChecklistItem

MAX_ITEMS = 50
TEXT_MAX_LENGTH = ChecklistItem._meta.get_field('text').max_length
# fromisoformat은 2026-W01-1 같은 주차 형식도 받아서 달력 날짜 형식을 먼저 확인한다.
DUE_PATTERN = re.compile(r'\d{4}-\d{2}-\d{2}')


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _text_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item) for item in value if _text(item)]


def _due(value):
    """YYYY-MM-DD 문자열만 날짜로 바꾼다. 그 외 형식은 None."""
    if not isinstance(value, str) or not DUE_PATTERN.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def clean_checklist(checklist):
    """checklist 원본을 ChecklistItem 생성 인자 목록으로 바꾼다.

    목록이 아니면 빈 목록, 딕셔너리가 아니거나 text가 빈 항목은 뺀다.
    state가 허용 값이 아니면 CHECK_NEEDED, due 형식이 틀리면 None으로 둔다. done은 항상 False로 시작한다.
    """
    if not isinstance(checklist, (list, tuple)):
        return []
    cleaned = []
    for raw in checklist:
        if not isinstance(raw, dict) or not _text(raw.get('text')):
            continue
        state = raw.get('state')
        cleaned.append({
            'order': len(cleaned),
            'text': _text(raw.get('text'))[:TEXT_MAX_LENGTH],
            'detail': _text(raw.get('detail')),
            'docs': _text_list(raw.get('docs')),
            'method': _text(raw.get('method')),
            'due': _due(raw.get('due')),
            'state': state if state in ChecklistItem.State.values else ChecklistItem.State.CHECK_NEEDED,
            'note': _text(raw.get('note')),
            'source_ids': _text_list(raw.get('sourceIds', raw.get('source_ids'))),
        })
        if len(cleaned) == MAX_ITEMS:
            break
    return cleaned


def replace_checklist(document, checklist):
    """문서의 체크리스트를 새 항목으로 교체한다. 호출하는 쪽의 트랜잭션 안에서 실행한다."""
    document.checklist_items.all().delete()
    ChecklistItem.objects.bulk_create(
        ChecklistItem(document=document, **fields) for fields in clean_checklist(checklist)
    )
