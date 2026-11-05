from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework.exceptions import NotFound

from notifications.models import NotificationPreference, PushDevice, registration_digest


@sensitive_variables('registration_id')
@transaction.atomic
def register_device(*, user, registration_id, registration_type, platform, replaces_device_id=None):
    digest = registration_digest(registration_id)
    # 갱신 대상들을 PK 순으로 잠가 계정 전환/식별자 교체의 락 순서를 통일한다.
    locked = list(PushDevice.objects.select_for_update().filter(
        Q(registration_hash=digest) | Q(pk=replaces_device_id),
    ).order_by('pk'))
    previous = None
    if replaces_device_id is not None:
        previous = next((d for d in locked if d.pk == replaces_device_id and d.user_id == user.pk), None)
        if previous is None:
            raise NotFound()
    device, created = PushDevice.objects.select_for_update().get_or_create(
        registration_hash=digest,
        defaults={'user': user, 'registration_id': registration_id,
                  'registration_type': registration_type, 'platform': platform},
    )
    if not created:
        if device.user_id != user.pk:
            device.last_success_at = None
        device.user = user
        device.registration_id = registration_id
        device.registration_type = registration_type
        device.platform = platform
        device.is_active = True
        device.save()
    if previous is not None and previous.pk != device.pk:
        PushDevice.objects.filter(pk=previous.pk, user=user).update(is_active=False, updated_at=timezone.now())
    return device


@transaction.atomic
def set_preference(user, enabled):
    preference, _ = NotificationPreference.objects.select_for_update().get_or_create(user=user)
    if enabled and not preference.enabled:
        preference.enabled_since = timezone.now()
    elif not enabled:
        preference.enabled_since = None
    preference.enabled = enabled
    preference.save(update_fields=['enabled', 'enabled_since', 'updated_at'])
    return preference


def record_device_result(device, result):
    """이전 계정/등록 세대에 대한 늦은 응답이 최신 등록을 변경하지 않도록 한다."""
    from .push_sender import PushStatus

    queryset = PushDevice.objects.filter(
        pk=device.pk, user_id=device.user_id, registration_hash=device.registration_hash,
        updated_at=device.updated_at, is_active=True,
    )
    if result.status == PushStatus.ACCEPTED:
        return queryset.update(last_success_at=timezone.now())
    if result.status == PushStatus.INVALID_DEVICE:
        return queryset.update(is_active=False, updated_at=timezone.now())
    return 0
