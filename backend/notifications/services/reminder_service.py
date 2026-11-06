"""Schedule 기반 마감 알림의 대상 조회와 DB 점유를 담당한다."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from documents.models import Schedule
from notifications.models import DeadlineReminder, NotificationPreference, PushDevice, ReminderDelivery
from notifications.services.devices import record_device_result
from notifications.services.push_sender import PushStatus

KOREA = ZoneInfo('Asia/Seoul')
REMINDER_DAYS = (7, 1)
RECLAIMABLE_STATUSES = (
    ReminderDelivery.Status.PENDING,
    ReminderDelivery.Status.RETRYABLE,
    ReminderDelivery.Status.CONFIGURATION_ERROR,
)


def reminder_time(deadline_date, days_before):
    """마감 날짜에서 7일 또는 1일을 뺀 한국시간 오전 9시를 계산한다."""
    if type(deadline_date) is not date or type(days_before) is not int or days_before not in REMINDER_DAYS:
        raise ValueError('마감일과 7 또는 1일 사전 알림을 지정하세요.')
    return datetime.combine(deadline_date - timedelta(days=days_before), time(9), tzinfo=KOREA)


def is_due_today(*, scheduled_at, now, created_at, enabled_since):
    """당일 지연 실행만 허용하고, 늦은 등록/동의에 소급 발송하지 않는다."""
    values = (scheduled_at, now, created_at, enabled_since)
    if enabled_since is None:
        return False
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError('시간대가 있는 시각이 필요합니다.')
    return (
        scheduled_at.astimezone(KOREA).date() == now.astimezone(KOREA).date()
        and created_at <= scheduled_at and enabled_since <= scheduled_at <= now
    )


@dataclass(frozen=True)
class ScheduleEvent:
    schedule: Schedule
    days_before: int


@dataclass(frozen=True)
class DeliveryClaim:
    """외부 FCM 호출에 필요한, 잠금 해제 뒤에도 안전한 DB 스냅샷."""

    delivery_id: int
    device: PushDevice
    schedule_id: int
    document_id: int
    deadline_date: date
    days_before: int


def due_schedule_events(*, today=None, now=None):
    """한국 날짜 기준 D-7/D-1이며 발송 자격을 갖춘 일정만 반환한다.

    `Schedule.owner == Document.owner` 계약이 손상된 행도 방어적으로 제외한다. 완료,
    당일, 기한 경과 일정은 ``date__in`` 조건 밖이어서 포함될 수 없다.
    """
    now = now or timezone.now()
    today = today or timezone.localdate()
    schedules = (
        Schedule.objects.filter(
            done=False,
            date__in=[today + timedelta(days=7), today + timedelta(days=1)],
            owner=F('document__owner'),
            owner__notification_preference__enabled=True,
        )
        .select_related('document', 'owner__notification_preference')
        .order_by('date', 'id')
    )
    events = []
    for schedule in schedules:
        preference = schedule.owner.notification_preference
        days_before = (schedule.date - today).days
        scheduled_at = reminder_time(schedule.date, days_before)
        if is_due_today(
            scheduled_at=scheduled_at,
            now=now,
            created_at=schedule.created_at,
            enabled_since=preference.enabled_since,
        ):
            events.append(ScheduleEvent(schedule=schedule, days_before=days_before))
    return events


def active_devices_for(schedule):
    """해당 일정 소유자의 활성 기기를 PK 순으로 반환한다."""
    return PushDevice.objects.filter(user_id=schedule.owner_id, is_active=True).order_by('pk')


def pending_delivery_count(event):
    """dry-run용: DB를 변경하지 않고 이번 실행에서 점유 가능한 기기 수를 센다."""
    reminder = DeadlineReminder.objects.filter(
        schedule_id=event.schedule.pk, days_before=event.days_before,
    ).first()
    devices = active_devices_for(event.schedule)
    if reminder is None:
        return devices.count()
    status_by_device = dict(ReminderDelivery.objects.filter(
        reminder=reminder, device__in=devices,
    ).values_list('device_id', 'status'))
    return sum(
        status_by_device.get(device.pk) in (None, *RECLAIMABLE_STATUSES)
        for device in devices
    )


def claim_delivery(*, schedule_id, days_before, device_id, today=None, now=None):
    """한 기기 발송을 짧은 트랜잭션에서 점유하고 FCM 호출용 스냅샷을 돌려준다.

    외부 호출은 반드시 이 함수가 반환된 뒤 수행한다. 완료/삭제/기기 해제와 경합하면
    여기서 최신 행을 다시 확인해 가능한 한 호출 전에 제외한다.
    """
    now = now or timezone.now()
    today = today or timezone.localdate()
    if days_before not in REMINDER_DAYS:
        raise ValueError('지원하지 않는 사전 알림 일수입니다.')

    with transaction.atomic():
        schedule = (
            Schedule.objects.select_for_update()
            .select_related('document')
            .filter(
                pk=schedule_id,
                done=False,
                date=today + timedelta(days=days_before),
                owner=F('document__owner'),
            )
            .first()
        )
        if schedule is None:
            return None

        preference = NotificationPreference.objects.select_for_update().filter(
            user_id=schedule.owner_id, enabled=True,
        ).first()
        if preference is None or not is_due_today(
            scheduled_at=reminder_time(schedule.date, days_before),
            now=now,
            created_at=schedule.created_at,
            enabled_since=preference.enabled_since,
        ):
            return None

        reminder, _ = DeadlineReminder.objects.get_or_create(
            schedule=schedule, days_before=days_before,
        )
        device = PushDevice.objects.select_for_update().filter(
            pk=device_id, user_id=schedule.owner_id, is_active=True,
        ).first()
        if device is None:
            return None
        delivery, _ = ReminderDelivery.objects.get_or_create(reminder=reminder, device=device)
        claimed = ReminderDelivery.objects.filter(
            pk=delivery.pk,
            status__in=RECLAIMABLE_STATUSES,
        ).update(
            status=ReminderDelivery.Status.PROCESSING,
            claimed_at=now,
            completed_at=None,
            error_code='',
            provider_message_id='',
            updated_at=now,
        )
        if not claimed:
            return None
        return DeliveryClaim(
            delivery_id=delivery.pk,
            device=device,
            schedule_id=schedule.pk,
            document_id=schedule.document_id,
            deadline_date=schedule.date,
            days_before=days_before,
        )


def delivery_is_current(claim):
    """외부 호출 직전 완료/삭제·수신 거부·기기 이전을 한 번 더 확인한다.

    확인 직후의 변경까지 FCM과 원자적으로 묶을 수는 없다. 이 검사에서 제외된 건은
    아직 외부 호출을 하지 않았으므로 PENDING으로 돌려 현재 조건에 따라 재평가한다.
    """
    now = timezone.now()
    today = timezone.localdate()
    scheduled_at = reminder_time(claim.deadline_date, claim.days_before)
    valid = (
        scheduled_at.date() == today
        and scheduled_at <= now
        and ReminderDelivery.objects.filter(
            pk=claim.delivery_id,
            status=ReminderDelivery.Status.PROCESSING,
            reminder__schedule_id=claim.schedule_id,
            reminder__schedule__done=False,
            reminder__schedule__date=claim.deadline_date,
            reminder__schedule__owner_id=claim.device.user_id,
            reminder__schedule__document__owner_id=claim.device.user_id,
            reminder__schedule__owner__notification_preference__enabled=True,
            reminder__schedule__owner__notification_preference__enabled_since__lte=scheduled_at,
            device_id=claim.device.pk,
            device__user_id=claim.device.user_id,
            device__is_active=True,
            device__updated_at=claim.device.updated_at,
            device__registration_hash=claim.device.registration_hash,
        ).exists()
    )
    if not valid:
        ReminderDelivery.objects.filter(
            pk=claim.delivery_id, status=ReminderDelivery.Status.PROCESSING,
        ).update(status=ReminderDelivery.Status.PENDING, claimed_at=None, updated_at=now)
    return valid


def complete_delivery(claim, result):
    """FCM 결과를 기록하고, 아직 살아 있는 현재 기기만 갱신한다.

    FCM이 수락한 직후 프로세스가 끝나거나 DB 기록이 실패하면 정확히 한 번 전송을
    보장할 수 없다. 이 경우 재발송 위험을 피하기 위해 PROCESSING/UNKNOWN을 자동으로
    회수하지 않는다.
    """
    status = {
        PushStatus.ACCEPTED: ReminderDelivery.Status.SENT,
        PushStatus.INVALID_DEVICE: ReminderDelivery.Status.INVALID_DEVICE,
        PushStatus.RETRYABLE: ReminderDelivery.Status.RETRYABLE,
        PushStatus.CONFIGURATION_ERROR: ReminderDelivery.Status.CONFIGURATION_ERROR,
        PushStatus.PERMANENT_ERROR: ReminderDelivery.Status.FAILED,
        PushStatus.UNKNOWN: ReminderDelivery.Status.UNKNOWN,
    }[result.status]
    completed_at = timezone.now()
    completed = ReminderDelivery.objects.filter(
        pk=claim.delivery_id, status=ReminderDelivery.Status.PROCESSING,
    ).update(
        status=status,
        provider_message_id=result.provider_message_id if result.status == PushStatus.ACCEPTED else '',
        error_code=result.error_code,
        completed_at=completed_at,
        updated_at=completed_at,
    )
    if completed:
        record_device_result(claim.device, result)
    return bool(completed)
