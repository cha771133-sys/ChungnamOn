"""기본 7일·30일 집계. 조회는 DB를 변경하지 않으며 결측을 0%로 바꾸지 않는다."""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.utils import timezone

from accounts.models import UserProfile
from chatbot.models import ChatMessage
from documents.models import Document, InterpretationResult

from ..models import KPIEvent


def _metric(key, label, note, numerator=None, denominator=None, status=None):
    return {
        'metric': key, 'label': label, 'numerator': numerator, 'denominator': denominator,
        'rate': round(numerator / denominator * 100, 2) if denominator else None,
        'status': status or ('AVAILABLE' if denominator else 'NO_DATA'), 'note': note,
    }


def get_kpi_summary(*, days=7, now=None, language=None):
    if type(days) is not int or days not in (7, 30):
        raise ValidationError('집계 기간은 7일 또는 30일이어야 합니다.')
    if language is not None and language not in ['', *UserProfile.Language.values]:
        raise ValidationError('실제 지원 언어 또는 언어 미확인을 선택하세요.')
    end = now or timezone.now()
    if timezone.is_naive(end):
        raise ValidationError('집계 종료 시각에는 시간대가 필요합니다.')
    start = end - timedelta(days=days)
    real = KPIEvent.objects.filter(data_kind=KPIEvent.DataKind.REAL, document__isnull=False)
    # 최초 실측 분석 시작을 기준으로 문서당 한 번만 분모에 넣는다. 재시도는 새 분모가 아니다.
    first_start = real.filter(document_id=OuterRef('pk'), event_type=KPIEvent.Type.ANALYSIS_STARTED).order_by('occurred_at', 'id')
    cohort = Document.objects.annotate(
        first_started=Subquery(first_start.values('occurred_at')[:1]),
        analysis_language=Subquery(first_start.values('language')[:1]),
    ).filter(first_started__gte=start, first_started__lt=end)
    if language is not None:
        cohort = cohort.filter(analysis_language=language)
    completion = real.filter(
        document_id=OuterRef('pk'), event_type=KPIEvent.Type.INTERPRETATION_COMPLETED,
        occurred_at__gte=OuterRef('first_started'), occurred_at__lt=end,
        properties__has_translation=True, properties__has_summary=True,
    )
    provided = real.filter(document_id=OuterRef('pk'), event_type=KPIEvent.Type.RESULT_PROVIDED,
                           occurred_at__gte=OuterRef('first_started'), occurred_at__lt=end)
    failed = real.filter(document_id=OuterRef('pk'), event_type=KPIEvent.Type.INTERPRETATION_FAILED,
                         occurred_at__gte=OuterRef('first_started'), occurred_at__lt=end)
    results = InterpretationResult.objects.filter(document_id=OuterRef('pk')).exclude(
        translated_text__regex=r'^\s*$',
    ).exclude(summary=[])
    counts = cohort.annotate(
        observed_completed=Exists(completion), observed_provided=Exists(provided), observed_failed=Exists(failed),
        has_result=Exists(results),
    ).aggregate(
        total=Count('pk'), completed=Count('pk', filter=Q(observed_completed=True, has_result=True)),
        provided=Count('pk', filter=Q(observed_completed=True, has_result=True, observed_provided=True)),
        failed=Count('pk', filter=Q(observed_failed=True)),
    )
    events = real.filter(occurred_at__gte=start, occurred_at__lt=end)
    if language is not None:
        events = events.filter(language=language)
    # 생성 답변 모델과 연결된 저장 이벤트만 분모. 질문 요청/AI 실패는 답변이 아니다.
    answers = events.filter(event_type=KPIEvent.Type.CHAT_ANSWER_CREATED).annotate(
        has_answer=Exists(ChatMessage.objects.filter(pk=OuterRef('entity_id'), document_id=OuterRef('document_id'))),
    ).filter(has_answer=True)
    chat = answers.aggregate(
        total=Count('entity_id', distinct=True),
        grounded=Count('entity_id', distinct=True, filter=Q(
            properties__status='GROUNDED', properties__grounded=True, properties__official_source_count__gt=0)),
        unverified=Count('entity_id', distinct=True, filter=Q(properties__status='UNVERIFIED')),
        mismatch=Count('entity_id', distinct=True, filter=Q(properties__status='MISMATCH')),
    )
    metrics = [
        _metric('document_interpretation_completion_rate', '문서 해석 완료율',
                '부분 측정: 서버 해석 처리 완료. 최초 실측 분석 시작 문서 기준, 재시도·OCR 실패 포함. '
                '종료 전 완료 이벤트와 현재 번역·요약 존재를 함께 확인한다. 화면 표시를 증명하지 않는다.',
                counts['completed'], counts['total'], 'PARTIAL' if counts['total'] else 'NO_DATA'),
        _metric('verification_match_rate', '교차 검증 일치율',
                '검증 항목 모델·항목별 대조 시도 계약이 없다. 문서 단위 더미 verification JSON을 항목 수로 추측하지 않는다.',
                status='NOT_MEASURABLE'),
        _metric('checklist_completion_rate', '체크리스트 완료율',
                'Checklist/ChecklistItem 모델이 없다. 사용자 완료 표시는 실제 행정업무 완료의 증거가 아니다.',
                status='NOT_MEASURABLE'),
        _metric('deadline_schedule_rate', '마감일 등록률',
                'Schedule 모델과 마감일 확인 여부 계약이 없다. deadlines 단순 추출값을 확정 기한으로 간주하지 않는다.',
                status='NOT_MEASURABLE'),
        _metric('chat_grounded_answer_rate', '챗봇 근거 답변 비율',
                '실측 저장 이벤트와 연결된 생성 답변 기준. 공식자료 ID·승인·URL·본문 해시·확인일을 저장 시 검증한다. '
                '더미·테스트·출처 미확인 제외. 근거 연결은 답변 전체의 정확도를 보장하지 않는다.',
                chat['grounded'], chat['total']),
        _metric('user_satisfaction', '사용자 만족도 (내용 이해도·실제 도움)',
                '기존 만족도 수집 모델/API가 없다. 척도·프론트 계약 합의 후 후속 작업으로 분리하며 점수나 응답 수를 만들지 않는다.',
                status='NOT_MEASURABLE'),
    ]
    supplied = _metric('document_result_provided_rate', '결과 API 제공 비율',
                       '부분 측정: 성공한 결과 API 응답 생성 사실만 기록한다. 실제 수신·화면 표시 계약이 없어 화면 표시율은 NOT_MEASURABLE이다.',
                       counts['provided'], counts['total'], 'PARTIAL' if counts['total'] else 'NO_DATA')
    # 이력 누락/분류 제외는 운영 데이터로 보정하거나 과거 이벤트로 역생성하지 않는다.
    all_period_events = KPIEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
    diagnostics = dict(all_period_events.values('data_kind').order_by().annotate(count=Count('pk')).values_list('data_kind', 'count'))
    period_documents = Document.objects.filter(created_at__gte=start, created_at__lt=end)
    recorded_start = KPIEvent.objects.filter(document_id=OuterRef('pk'), event_type=KPIEvent.Type.ANALYSIS_STARTED)
    period_answers = ChatMessage.objects.filter(created_at__gte=start, created_at__lt=end)
    recorded_answer = KPIEvent.objects.filter(event_type=KPIEvent.Type.CHAT_ANSWER_CREATED,
                                             entity_id=OuterRef('pk'), document_id=OuterRef('document_id'))
    return {
        'days': days, 'start': start, 'end': end, 'language': language, 'metrics': metrics, 'result_provided': supplied,
        'document_states': counts, 'chat_states': chat,
        'verification_states': {'status': 'NOT_MEASURABLE'},
        'chat_withheld': {'status': 'NOT_MEASURABLE', 'note': '답변 보류/AI 호출 실패를 구분하는 기존 저장 계약이 없다.'},
        'data_kinds': diagnostics,
        'documents_without_start_events': period_documents.exclude(status='PENDING').annotate(
            recorded=Exists(recorded_start)).filter(recorded=False).count(),
        'answers_without_events': period_answers.annotate(recorded=Exists(recorded_answer)).filter(recorded=False).count(),
        'measurement_started_at': KPIEvent.objects.order_by('created_at').values_list('created_at', flat=True).first(),
        'retention_days': getattr(settings, 'KPI_EVENT_RETENTION_DAYS', None),
    }
