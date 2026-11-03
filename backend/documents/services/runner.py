"""문서 처리 실행 진입점.

지금은 threading으로 백그라운드 실행한다. Celery로 바꿀 때는 start_processing 내용만 교체한다.

설정값 (settings에 없으면 기본값 사용)
- DOCUMENTS_PROCESS_SYNC: True면 백그라운드 대신 바로 실행 (테스트용). 기본 False
"""

import threading

from django.conf import settings
from django.db import connections

from ..models import Document
from .pipeline import STARTABLE_STATUSES, process_document


def start_processing(document_id):
    """문서 처리를 시작한다. 이미 PROCESSING·DONE이면 시작하지 않고 False를 반환한다."""
    if not Document.objects.filter(id=document_id, status__in=STARTABLE_STATUSES).exists():
        return False

    if getattr(settings, 'DOCUMENTS_PROCESS_SYNC', False):
        process_document(document_id)
    else:
        threading.Thread(target=_run_in_thread, args=(document_id,), daemon=True).start()
    return True


def _run_in_thread(document_id):
    try:
        process_document(document_id)
    finally:
        # 스레드마다 열린 DB 연결을 닫아 연결이 쌓이지 않게 한다.
        connections.close_all()
