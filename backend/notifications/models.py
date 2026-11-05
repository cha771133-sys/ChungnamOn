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
        self.registration_hash = registration_digest(self.registration_id)
        if kwargs.get('update_fields') is not None and 'registration_id' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'registration_hash'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f'푸시 기기 {self.pk} (사용자 {self.user_id})'
