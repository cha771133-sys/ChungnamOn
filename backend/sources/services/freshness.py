from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db.models import Case, DateTimeField, DurationField, Exists, ExpressionWrapper, F, IntegerField, OuterRef, Q, Value, When
from django.db.models.functions import Cast, Coalesce


# 운영 정책 미확정: 법규 7일, 지원정책 3일, 일반 안내 14일을 구현 기본값으로 사용한다.
DEFAULT_INTERVAL_HOURS = {'law': 168, 'public_data': 72, 'html': 336}


def interval_hours(target):
    configured = getattr(settings, 'OFFICIAL_SOURCE_INTERVAL_HOURS', {})
    value = target.interval_hours or configured.get(target.provider, DEFAULT_INTERVAL_HOURS[target.provider])
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError('재확인 주기는 양의 정수 시간이어야 합니다.')
    return value


def next_due_at(target):
    if target.failures and target.retry_after:
        return target.retry_after
    checked = target.last_success_at
    if checked is None and target.document_id:
        checked = target.document.last_success_at or target.document.collected_at
    return checked + timedelta(hours=interval_hours(target)) if checked else None


def is_due(target, now=None):
    now = now or timezone.now()
    if not target.enabled or (target.lease_expires_at and target.lease_expires_at > now):
        return False
    due = next_due_at(target)
    return due is None or due <= now


def freshness(document, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    date_attention = (document.effective_at and document.effective_at > today) or (document.published_at and document.published_at > today)
    if date_attention or document.revisions.filter(review_status='PENDING_REVIEW').exists() or document.review_status != 'APPROVED':
        return 'REVIEW_REQUIRED'
    target = getattr(document, 'refresh_target', None)
    if target and target.failures:
        return 'REFRESH_FAILED'
    checked = document.last_success_at or document.collected_at
    hours = interval_hours(target) if target else DEFAULT_INTERVAL_HOURS[document.provider]
    if not checked or checked + timedelta(hours=hours) <= now:
        return 'STALE'
    return 'CURRENT'


def freshness_details(document, now=None):
    """날짜·검토·실패를 별도로 노출하며 법적 효력 상태는 추론하지 않는다."""
    target = getattr(document, 'refresh_target', None)
    return {
        'status': freshness(document, now), 'published_at': document.published_at,
        'effective_at': document.effective_at, 'review_status': document.review_status,
        'last_success_at': document.last_success_at or document.collected_at,
        'refresh_failed': bool(target and target.failures),
        'next_due_at': next_due_at(target) if target else None,
        'legal_effect': document.metadata.get('legal_effect', '확인 필요'),
    }


def filter_freshness(queryset, state, now=None):
    """관리자 필터는 모든 자료를 Python으로 로드하지 않고 DB에서 계산한다."""
    from sources.models import SourceRevision
    now = now or timezone.now()
    pending = SourceRevision.objects.filter(document_id=OuterRef('pk'), review_status='PENDING_REVIEW')
    custom_period = ExpressionWrapper(Cast(F('refresh_target__interval_hours'), IntegerField()) * Value(timedelta(hours=1)), output_field=DurationField())
    defaults = getattr(settings, 'OFFICIAL_SOURCE_INTERVAL_HOURS', {})
    default_period = Case(*[When(provider=provider, then=Value(timedelta(hours=defaults.get(provider, hours)))) for provider, hours in DEFAULT_INTERVAL_HOURS.items()], output_field=DurationField())
    queryset = queryset.annotate(
        _pending=Exists(pending),
        _expires=ExpressionWrapper(Coalesce('last_success_at', 'collected_at') + Coalesce(custom_period, default_period), output_field=DateTimeField()),
    )
    review = Q(_pending=True) | ~Q(review_status='APPROVED') | Q(effective_at__gt=timezone.localdate(now)) | Q(published_at__gt=timezone.localdate(now))
    failed = Q(refresh_target__failures__gt=0)
    stale = Q(_expires__isnull=True) | Q(_expires__lte=now)
    conditions = {'REVIEW_REQUIRED': review, 'REFRESH_FAILED': ~review & failed, 'STALE': ~review & ~failed & stale, 'CURRENT': ~review & ~failed & ~stale}
    return queryset.filter(conditions[state]) if state in conditions else queryset
