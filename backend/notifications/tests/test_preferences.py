from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from notifications.models import NotificationPreference
from .helpers import NotificationAPITestCase


class PreferenceTests(NotificationAPITestCase):
    path = '/api/notifications/preferences/'

    def test_unset_is_disabled_and_get_has_no_side_effects(self):
        self.assertEqual(self.client.get(self.path).data, {'enabled': False, 'registeredDeviceCount': 0})
        self.assertFalse(NotificationPreference.objects.exists())

    def test_enable_disable_and_active_device_count(self):
        device_id = self.register().data['id']
        self.assertEqual(self.client.patch(self.path, {'enabled': True}, format='json').data,
                         {'enabled': True, 'registeredDeviceCount': 1})
        preference = NotificationPreference.objects.get(user=self.user)
        self.assertIsNotNone(preference.enabled_since)
        self.assertEqual(self.client.patch(self.path, {'enabled': False}, format='json').status_code, 200)
        preference.refresh_from_db()
        self.assertFalse(preference.enabled)
        self.assertIsNone(preference.enabled_since)
        self.client.delete(f'/api/notifications/devices/{device_id}/')
        self.assertEqual(self.client.get(self.path).data['registeredDeviceCount'], 0)

    def test_reenable_records_new_consent_and_repeated_true_preserves_it(self):
        moment = datetime(2026, 11, 13, tzinfo=timezone.utc)
        with patch('django.utils.timezone.now', return_value=moment):
            self.client.patch(self.path, {'enabled': True}, format='json')
        with patch('django.utils.timezone.now', return_value=moment + timedelta(hours=1)):
            self.client.patch(self.path, {'enabled': True}, format='json')
        self.assertEqual(NotificationPreference.objects.get().enabled_since, moment)
        self.client.patch(self.path, {'enabled': False}, format='json')
        later = moment + timedelta(days=1)
        with patch('django.utils.timezone.now', return_value=later):
            self.client.patch(self.path, {'enabled': True}, format='json')
        preference = NotificationPreference.objects.get()
        self.assertEqual(preference.enabled_since, later)
        self.assertEqual(preference.updated_at, later)

    def test_only_boolean_is_allowed(self):
        for value in ('true', 'false', 1, 0, None, [], {}):
            with self.subTest(value=value):
                response = self.client.patch(self.path, {'enabled': value}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'VALIDATION_ERROR')
        self.assertEqual(self.client.patch(self.path, {}, format='json').status_code, 400)
        self.assertFalse(NotificationPreference.objects.exists())

    def test_cannot_target_another_user(self):
        response = self.client.patch(self.path, {'enabled': True, 'userId': self.other.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.client.patch(self.path, {'enabled': True}, format='json')
        self.authenticate(self.other)
        self.assertFalse(self.client.get(self.path).data['enabled'])
        self.client.patch(self.path, {'enabled': False}, format='json')
        self.assertTrue(NotificationPreference.objects.get(user=self.user).enabled)

    def test_anonymous_cannot_read_or_change(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.path).status_code, 401)
        self.assertEqual(self.client.patch(self.path, {'enabled': True}, format='json').status_code, 401)
