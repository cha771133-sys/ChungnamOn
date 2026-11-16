from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from notifications.models import DeadlineReminder, NotificationPreference, PushDevice, registration_digest
from .helpers import FID
from .test_command import BEFORE_DUE, TODAY


class ReminderUpgradeTests(TransactionTestCase):
    def test_upgrade_preserves_existing_devices_preferences_and_schedules_without_guessing_links(self):
        """연동 전 데이터가 있는 DB에 신규 FK 테이블을 적용해도 기존 기록을 변경하지 않는다."""
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([('notifications', '0001_initial')])
            old_apps = executor.loader.project_state([
                ('notifications', '0001_initial'), ('documents', '0003_schedule'),
            ]).apps
            User = old_apps.get_model('accounts', 'User')
            Device = old_apps.get_model('notifications', 'PushDevice')
            Preference = old_apps.get_model('notifications', 'NotificationPreference')
            Document = old_apps.get_model('documents', 'Document')
            Schedule = old_apps.get_model('documents', 'Schedule')
            user = User.objects.create(email='migration-reminder@example.com')
            document = Document.objects.create(
                owner=user, file='documents/existing.pdf', file_name='existing.pdf',
                file_type='PDF', expires_at=BEFORE_DUE,
            )
            schedule = Schedule.objects.create(owner=user, document=document, title='기존 일정', date=TODAY)
            device = Device.objects.create(
                user=user, registration_id=FID, registration_hash=registration_digest(FID), platform='WEB',
            )
            preference = Preference.objects.create(user=user, enabled=True, enabled_since=BEFORE_DUE)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            self.assertEqual(PushDevice.objects.get(pk=device.pk).registration_id, FID)
            self.assertEqual(NotificationPreference.objects.get(pk=preference.pk).enabled_since, BEFORE_DUE)
            from documents.models import Schedule as CurrentSchedule
            self.assertTrue(CurrentSchedule.objects.filter(pk=schedule.pk).exists())
            self.assertFalse(DeadlineReminder.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
