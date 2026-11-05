from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from notifications.models import NotificationPreference, PushDevice, registration_digest
from notifications.services.devices import register_device, record_device_result
from notifications.services.push_sender import PushResult, PushStatus
from .helpers import FID, OTHER_FID


class ModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='model@example.com')
        self.device = register_device(user=self.user, registration_id=FID, registration_type='FID', platform='WEB')

    def test_hash_and_device_constraint(self):
        self.assertEqual(self.device.registration_hash, registration_digest(FID))
        with self.assertRaises(IntegrityError), transaction.atomic():
            PushDevice.objects.create(user=self.user, registration_id=FID, platform='WEB')
        self.assertNotIn(FID, str(self.device))

    def test_preference_default_unique_and_consent_constraint(self):
        preference = NotificationPreference.objects.create(user=self.user)
        self.assertFalse(preference.enabled)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationPreference.objects.create(user=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationPreference.objects.filter(pk=preference.pk).update(enabled=True)

    def test_registration_type_database_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PushDevice.objects.create(user=self.user, registration_id=OTHER_FID, registration_type='TOKEN', platform='WEB')

    def test_unregistered_result_deactivates(self):
        self.assertEqual(record_device_result(self.device, PushResult(PushStatus.INVALID_DEVICE, 'UNREGISTERED')), 1)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)

    def test_success_tracks_acceptance_without_changing_registration_version(self):
        old_updated_at = self.device.updated_at
        record_device_result(self.device, PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/1'))
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_success_at)
        self.assertEqual(self.device.updated_at, old_updated_at)

    def test_configuration_or_uncertain_failures_do_not_disable_devices(self):
        for status in (PushStatus.CONFIGURATION_ERROR, PushStatus.UNKNOWN, PushStatus.RETRYABLE, PushStatus.PERMANENT_ERROR):
            self.assertEqual(record_device_result(self.device, PushResult(status)), 0)
            self.device.refresh_from_db()
            self.assertTrue(self.device.is_active)

    def test_late_result_cannot_modify_new_owner_or_registration(self):
        stale = self.device
        other = get_user_model().objects.create_user(email='new-owner@example.com')
        new = register_device(user=other, registration_id=FID, registration_type='FID', platform='WEB')
        self.assertEqual(record_device_result(stale, PushResult(PushStatus.INVALID_DEVICE)), 0)
        self.assertEqual(record_device_result(stale, PushResult(PushStatus.ACCEPTED)), 0)
        new.refresh_from_db()
        self.assertTrue(new.is_active)
        self.assertIsNone(new.last_success_at)

    def test_late_result_cannot_disable_same_user_reregistration(self):
        stale = self.device
        register_device(user=self.user, registration_id=FID, registration_type='FID', platform='WEB')
        self.assertEqual(record_device_result(stale, PushResult(PushStatus.INVALID_DEVICE)), 0)


class ConcurrentRegistrationTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_accounts_share_one_registration(self):
        user_ids = [get_user_model().objects.create_user(email=f'race{i}@example.com').pk for i in range(2)]
        barrier = Barrier(2)

        def register(user_id):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=10)
                return register_device(user=user, registration_id=FID, registration_type='FID', platform='WEB').pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(register, user_ids))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(PushDevice.objects.count(), 1)
        self.assertEqual(PushDevice.objects.filter(is_active=True).count(), 1)
