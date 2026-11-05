from unittest.mock import patch

from django.db import OperationalError

from notifications.models import NotificationPreference, PushDevice
from .helpers import FID, OTHER_FID, NotificationAPITestCase


class DeviceAPITests(NotificationAPITestCase):
    def test_register_uses_jwt_owner_and_never_returns_identifier(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushDevice.objects.get().user, self.user)
        self.assertNotIn(FID, response.content.decode())
        self.assertNotIn('registrationId', response.data)
        self.assertFalse(NotificationPreference.objects.filter(user=self.user, enabled=True).exists())

    def test_anonymous_cannot_register(self):
        self.client.credentials()
        self.assertEqual(self.register().status_code, 401)
        self.assertEqual(PushDevice.objects.count(), 0)

    def test_reregister_is_idempotent_and_reactivates(self):
        first = self.register().data
        self.client.delete(f'/api/notifications/devices/{first["id"]}/')
        second = self.register(platform='ANDROID').data
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(PushDevice.objects.count(), 1)
        self.assertTrue(second['isActive'])
        self.assertEqual(second['platform'], 'ANDROID')

    def test_multiple_devices_per_user(self):
        self.register()
        self.register(OTHER_FID)
        self.assertEqual(PushDevice.objects.filter(user=self.user, is_active=True).count(), 2)

    def test_account_transfer_revokes_old_account_access(self):
        original = self.register().data
        self.authenticate(self.other)
        transferred = self.register().data
        self.assertEqual(original['id'], transferred['id'])
        self.assertEqual(PushDevice.objects.count(), 1)
        self.assertEqual(PushDevice.objects.get().user, self.other)
        self.authenticate(self.user)
        self.assertEqual(self.client.delete(f'/api/notifications/devices/{original["id"]}/').status_code, 404)
        self.assertTrue(PushDevice.objects.get().is_active)
        self.assertEqual(self.client.get('/api/notifications/preferences/').data['registeredDeviceCount'], 0)

    def test_deactivate_repeat_is_safe(self):
        """기기 해제 재요청이 행 삭제나 오류 없이 같은 결과를 내는지 검증한다."""
        device_id = self.register().data['id']
        for _ in range(2):
            self.assertEqual(self.client.delete(f'/api/notifications/devices/{device_id}/').status_code, 204)
        self.assertFalse(PushDevice.objects.get().is_active)

    def test_rotation_deactivates_previous_own_registration(self):
        old_id = self.register().data['id']
        new = self.register(OTHER_FID, replacesDeviceId=old_id)
        self.assertEqual(new.status_code, 200)
        self.assertNotEqual(new.data['id'], old_id)
        self.assertFalse(PushDevice.objects.get(pk=old_id).is_active)
        self.assertEqual(PushDevice.objects.filter(is_active=True).count(), 1)

    def test_rotation_same_identifier_stays_active(self):
        old_id = self.register().data['id']
        self.assertEqual(self.register(replacesDeviceId=old_id).status_code, 200)
        self.assertTrue(PushDevice.objects.get().is_active)

    def test_cannot_replace_another_users_device(self):
        old_id = self.register().data['id']
        self.authenticate(self.other)
        response = self.register(OTHER_FID, replacesDeviceId=old_id)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PushDevice.objects.count(), 1)

    def test_invalid_contract_and_unknown_owner_fields_are_rejected(self):
        """지원하지 않는 등록 방식·입력 형식·사용자 조작 필드를 공통 400으로 차단한다."""
        for data in (
            {'registrationType': 'TOKEN'}, {'registrationType': 'OTHER'}, {'platform': 'DESKTOP'},
            {'user': self.other.pk}, {'userId': self.other.pk}, {'isActive': True},
            {'replacesDeviceId': 0}, {'registrationId': ' ' + FID}, {'registrationId': 'x' * 4097},
            {'registrationId': ''}, {'registrationId': 'contains/private/value'},
        ):
            with self.subTest(data=list(data)):
                response = self.register(**data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'VALIDATION_ERROR')
        self.assertFalse(PushDevice.objects.exists())

    def test_storage_failure_does_not_leak_identifier(self):
        with patch('notifications.views.register_device', side_effect=OperationalError(FID)), \
                self.assertLogs('notifications.views', level='ERROR') as logs:
            response = self.register()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(FID, response.content.decode())
        self.assertEqual(logs.output, ['ERROR:notifications.views:알림 저장소 오류: OperationalError'])
        self.assertIsNone(logs.records[0].exc_info)
        self.assertNotIn(FID, str(logs.output))

    def test_no_arbitrary_push_or_device_list_api(self):
        self.assertEqual(self.client.get('/api/notifications/devices/').status_code, 405)
        self.assertEqual(self.client.post('/api/notifications/send/', {}, format='json').status_code, 404)
