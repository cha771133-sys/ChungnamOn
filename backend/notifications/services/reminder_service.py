"""Schedule 계약과 무관하게 검증 가능한 날짜 계산만 제공한다. 실제 대상 조회는 아직 없다."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KOREA = ZoneInfo('Asia/Seoul')


def reminder_time(deadline_date, days_before):
    """마감 날짜에서 7일 또는 1일을 뺀 한국시간 오전 9시를 계산한다."""
    if type(deadline_date) is not date or type(days_before) is not int or days_before not in (7, 1):
        raise ValueError('마감일과 7 또는 1일 사전 알림을 지정하세요.')
    return datetime.combine(deadline_date - timedelta(days=days_before), time(9), tzinfo=KOREA)


def is_due_today(*, scheduled_at, now, created_at, enabled_since):
    """당일 지연 실행만 허용한다. 일정/동의가 예정 시각 이후 생성되면 소급하지 않는다.

    소유권·완료·기기·마감일 변경 검증을 대신하지 않는다. 실제 Schedule 연동은 후속 작업이다.
    """
    values = (scheduled_at, now, created_at, enabled_since)
    if enabled_since is None:
        return False
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError('시간대가 있는 시각이 필요합니다.')
    return (
        scheduled_at.astimezone(KOREA).date() == now.astimezone(KOREA).date()
        and created_at <= scheduled_at and enabled_since <= scheduled_at <= now
    )
