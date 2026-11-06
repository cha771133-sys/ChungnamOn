import hashlib

from django.conf import settings
from django.db import models


def registration_digest(registration_id):
    """고엔트로피 FID의 동등 비교용 인덱스. 원문 암호화를 대체하지 않는다."""
    return hashlib.sha256(registration_id.encode('utf-8')).hexdigest()


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preference',
    )
    enabled = models.BooleanField(default=False)
    enabled_since = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(enabled=False, enabled_since__isnull=True)
                       | models.Q(enabled=True, enabled_since__isnull=False)),
            name='notification_consent_since_valid',
        )]

    def __str__(self):
        """개인정보 대신 설정 및 사용자 식별자만 표시한다."""
        return f'알림 설정 {self.pk} (사용자 {self.user_id})'


class PushDevice(models.Model):
    class RegistrationType(models.TextChoices):
        FID = 'FID', 'Firebase Installation ID'

    class Platform(models.TextChoices):
        WEB = 'WEB', '웹'
        ANDROID = 'ANDROID', '안드로이드'
        IOS = 'IOS', 'iOS'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_devices',
    )
    registration_id = models.TextField()
    registration_hash = models.CharField(max_length=64, unique=True, editable=False)
    registration_type = models.CharField(max_length=5, choices=RegistrationType.choices, default='FID')
    platform = models.CharField(max_length=7, choices=Platform.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'is_active'], name='push_device_user_active')]
        constraints = [
            models.CheckConstraint(condition=models.Q(registration_type='FID'), name='push_device_fid_only'),
            models.CheckConstraint(
                condition=models.Q(platform__in=['WEB', 'ANDROID', 'IOS']), name='push_device_platform_valid',
            ),
        ]

    def save(self, *args, **kwargs):
        """등록 원문 변경 시 비교 해시도 같은 저장 작업에 포함한다."""
        self.registration_hash = registration_digest(self.registration_id)
        if kwargs.get('update_fields') is not None and 'registration_id' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'registration_hash'}
        super().save(*args, **kwargs)

    def __str__(self):
        """로그·관리자 관계 표시에서 FID 원문과 해시가 노출되지 않게 한다."""
        return f'푸시 기기 {self.pk} (사용자 {self.user_id})'


class DeadlineReminder(models.Model):
    """Schedule의 D-7 또는 D-1 알림 이벤트.

    일정의 제목이나 문서 원문을 복제하지 않는다. Schedule의 날짜는 등록 뒤 변경할 수
    없으므로 ``schedule``과 ``days_before`` 조합이 알림 이벤트를 안정적으로 식별한다.
    """

    class DaysBefore(models.IntegerChoices):
        ONE = 1, 'D-1'
        SEVEN = 7, 'D-7'

    schedule = models.ForeignKey(
        'documents.Schedule', on_delete=models.CASCADE, related_name='deadline_reminders',
    )
    days_before = models.PositiveSmallIntegerField(choices=DaysBefore.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['schedule', 'days_before'], name='unique_deadline_reminder_per_event',
            ),
        ]
        indexes = [models.Index(fields=['schedule', 'days_before'], name='reminder_schedule_days_idx')]

    def __str__(self):
        return f'마감 알림 {self.pk} (일정 {self.schedule_id}, D-{self.days_before})'


class ReminderDelivery(models.Model):
    """한 알림 이벤트의 기기별 FCM 요청 결과.

    FCM 호출과 DB 갱신은 하나의 원자 작업이 될 수 없다. UNKNOWN 상태는 FCM이
    접수했을 가능성이 있으므로 자동 재시도하지 않고, 명시적으로 거절된 RETRYABLE만
    이후 명령 실행에서 다시 점유할 수 있다.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', '대기'
        PROCESSING = 'PROCESSING', '전송 중'
        SENT = 'SENT', 'FCM 접수'
        INVALID_DEVICE = 'INVALID_DEVICE', '무효 기기'
        RETRYABLE = 'RETRYABLE', '재시도 가능 오류'
        CONFIGURATION_ERROR = 'CONFIGURATION_ERROR', 'Firebase 설정 오류'
        FAILED = 'FAILED', '확정 실패'
        UNKNOWN = 'UNKNOWN', '결과 불명'

    reminder = models.ForeignKey(
        DeadlineReminder, on_delete=models.CASCADE, related_name='deliveries',
    )
    device = models.ForeignKey(PushDevice, on_delete=models.CASCADE, related_name='reminder_deliveries')
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['reminder', 'device'], name='unique_reminder_delivery_per_device',
            ),
        ]
        indexes = [
            models.Index(fields=['reminder', 'status'], name='delivery_reminder_status_idx'),
            models.Index(fields=['device', 'status'], name='delivery_device_status_idx'),
        ]

    def __str__(self):
        return f'알림 발송 {self.pk} ({self.status})'
