from datetime import date, datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from django.utils import timezone

from documents.models import Document, Schedule
from notifications.models import DeadlineReminder, NotificationPreference, ReminderDelivery
from notifications.services.devices import register_device
from notifications.services.push_sender import PushResult, PushStatus
from .helpers import FID, OTHER_FID

TODAY = date(2026, 8, 20)
NOW = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.get_current_timezone())
BEFORE_DUE = datetime(2026, 8, 1, 9, tzinfo=timezone.get_current_timezone())
COMMAND = 'notifications.management.commands.send_deadline_reminders'


class DeadlineReminderCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='reminder@example.com')
        self.document = Document.objects.create(
            owner=self.user,
            file='documents/reminder-test.pdf',
            file_name='reminder-test.pdf',
            file_type=Document.FileType.PDF,
            status=Document.Status.DONE,
            title='개인정보가 있는 문서 제목',
        )
        self.preference = NotificationPreference.objects.create(
            user=self.user, enabled=True, enabled_since=BEFORE_DUE,
        )
        self.device = register_device(
            user=self.user, registration_id=FID, registration_type='FID', platform='WEB',
        )

    def create_schedule(self, schedule_date, *, title='민감한 일정 제목', **fields):
        schedule = Schedule.objects.create(
            owner=self.user, document=self.document, date=schedule_date, title=title, **fields,
        )
        Schedule.objects.filter(pk=schedule.pk).update(created_at=BEFORE_DUE)
        schedule.refresh_from_db()
        return schedule

    def run_command(self, **options):
        output = StringIO()
        with patch('django.utils.timezone.localdate', return_value=TODAY), \
                patch('django.utils.timezone.now', return_value=NOW):
            call_command('send_deadline_reminders', stdout=output, **options)
        return output.getvalue()

    def test_d7_and_d1_send_real_schedule_ids_and_exclude_other_schedule_states(self):
        d7 = self.create_schedule(TODAY.replace(day=27), title='D-7 개인정보 일정')
        d1 = self.create_schedule(TODAY.replace(day=21), title='D-1 개인정보 일정')
        self.create_schedule(TODAY.replace(day=27), title='완료 일정', done=True)
        self.create_schedule(TODAY.replace(day=19), title='기한 경과 일정')
        self.create_schedule(TODAY, title='당일 일정')
        self.create_schedule(TODAY.replace(day=22), title='D-2 일정')
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/1')

        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            output = self.run_command()

        self.assertEqual(send.call_count, 2)
        self.assertEqual({call.kwargs['schedule_id'] for call in send.call_args_list}, {d7.pk, d1.pk})
        self.assertEqual({call.kwargs['days_before'] for call in send.call_args_list}, {7, 1})
        for call in send.call_args_list:
            self.assertEqual(call.kwargs['document_id'], self.document.pk)
            self.assertNotIn('title', call.kwargs)
        self.assertEqual(DeadlineReminder.objects.count(), 2)
        self.assertEqual(ReminderDelivery.objects.filter(status=ReminderDelivery.Status.SENT).count(), 2)
        self.assertIn('events=2', output)
        self.assertIn('accepted=2', output)

    def test_dry_run_has_no_fcm_or_database_side_effects(self):
        self.create_schedule(TODAY.replace(day=27))

        with patch(f'{COMMAND}.send_deadline_push') as send:
            output = self.run_command(dry_run=True)

        send.assert_not_called()
        self.assertFalse(DeadlineReminder.objects.exists())
        self.assertIn('dry-run deadline reminders: events=1 candidate_deliveries=1', output)

    def test_korean_nine_am_boundary_and_late_schedule_or_consent_are_not_retroactive(self):
        schedule = self.create_schedule(TODAY.replace(day=27))
        before_nine = NOW.replace(hour=8, minute=59)
        with patch(f'{COMMAND}.send_deadline_push') as send, \
                patch('django.utils.timezone.localdate', return_value=TODAY), \
                patch('django.utils.timezone.now', return_value=before_nine):
            call_command('send_deadline_reminders', stdout=StringIO())
        send.assert_not_called()
        self.assertFalse(DeadlineReminder.objects.exists())

        NotificationPreference.objects.filter(pk=self.preference.pk).update(enabled_since=NOW)
        with patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        self.assertFalse(DeadlineReminder.objects.exists())

        NotificationPreference.objects.filter(pk=self.preference.pk).update(enabled_since=BEFORE_DUE)
        Schedule.objects.filter(pk=schedule.pk).update(created_at=NOW)
        with patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        self.assertFalse(DeadlineReminder.objects.exists())

    def test_successful_delivery_is_not_resent_by_duplicate_command_execution(self):
        schedule = self.create_schedule(TODAY.replace(day=27))
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/1')

        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.run_command()
            self.run_command()

        send.assert_called_once()
        reminder = DeadlineReminder.objects.get(schedule=schedule, days_before=7)
        self.assertEqual(ReminderDelivery.objects.filter(reminder=reminder).count(), 1)
        self.assertEqual(reminder.deliveries.get().status, ReminderDelivery.Status.SENT)

    def test_multiple_active_devices_each_receive_one_delivery(self):
        schedule = self.create_schedule(TODAY.replace(day=21))
        second = register_device(
            user=self.user, registration_id=OTHER_FID, registration_type='FID', platform='ANDROID',
        )
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/1')

        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.run_command()
            self.run_command()

        self.assertEqual(send.call_count, 2)
        reminder = DeadlineReminder.objects.get(schedule=schedule, days_before=1)
        self.assertEqual(
            set(reminder.deliveries.values_list('device_id', flat=True)), {self.device.pk, second.pk},
        )

    def test_invalid_device_is_deactivated_and_never_marked_successful(self):
        schedule = self.create_schedule(TODAY.replace(day=27))
        invalid = PushResult(PushStatus.INVALID_DEVICE, error_code='UNREGISTERED')

        with patch(f'{COMMAND}.send_deadline_push', return_value=invalid):
            self.run_command()

        self.device.refresh_from_db()
        delivery = ReminderDelivery.objects.get(reminder__schedule=schedule)
        self.assertFalse(self.device.is_active)
        self.assertEqual(delivery.status, ReminderDelivery.Status.INVALID_DEVICE)
        self.assertFalse(delivery.provider_message_id)

    def test_firebase_configuration_failure_is_not_recorded_as_success(self):
        schedule = self.create_schedule(TODAY.replace(day=27))
        failure = PushResult(PushStatus.CONFIGURATION_ERROR, error_code='FIREBASE_CONFIGURATION')

        with patch(f'{COMMAND}.send_deadline_push', return_value=failure):
            output = self.run_command()

        delivery = ReminderDelivery.objects.get(reminder__schedule=schedule)
        self.assertEqual(delivery.status, ReminderDelivery.Status.CONFIGURATION_ERROR)
        self.assertFalse(delivery.provider_message_id)
        self.assertIn('accepted=0', output)
        self.assertIn('configuration_errors=1', output)

    def test_claim_prevents_a_second_worker_from_sending_the_same_device_event(self):
        """PROCESSING 상태는 결과 불명 재발송보다 중복 전송 방지를 우선한다."""
        from notifications.services.reminder_service import claim_delivery

        schedule = self.create_schedule(TODAY.replace(day=27))
        with patch('django.utils.timezone.localdate', return_value=TODAY), \
                patch('django.utils.timezone.now', return_value=NOW):
            first = claim_delivery(schedule_id=schedule.pk, days_before=7, device_id=self.device.pk)
            second = claim_delivery(schedule_id=schedule.pk, days_before=7, device_id=self.device.pk)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(ReminderDelivery.objects.get().status, ReminderDelivery.Status.PROCESSING)

    def test_schedule_document_and_user_deletion_cascade_reminders_and_deliveries(self):
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/1')
        schedule = self.create_schedule(TODAY.replace(day=27))
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted):
            self.run_command()
        schedule.delete()
        self.assertFalse(DeadlineReminder.objects.exists())
        self.assertFalse(ReminderDelivery.objects.exists())

        schedule = self.create_schedule(TODAY.replace(day=27))
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted):
            self.run_command()
        self.document.delete()
        self.assertFalse(Schedule.objects.filter(pk=schedule.pk).exists())
        self.assertFalse(DeadlineReminder.objects.exists())
        self.assertFalse(ReminderDelivery.objects.exists())

        document = Document.objects.create(
            owner=self.user, file='documents/user-cascade.pdf', file_name='user-cascade.pdf',
            file_type=Document.FileType.PDF, status=Document.Status.DONE,
        )
        schedule = Schedule.objects.create(
            owner=self.user, document=document, date=TODAY.replace(day=27), title='사용자 삭제',
        )
        DeadlineReminder.objects.create(schedule=schedule, days_before=7)
        self.user.delete()
        self.assertFalse(DeadlineReminder.objects.exists())

    def test_limit_and_invalid_limit(self):
        self.create_schedule(TODAY.replace(day=21), title='D-1')
        self.create_schedule(TODAY.replace(day=27), title='D-7')
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/1')

        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.run_command(limit=1)
        self.assertEqual(send.call_count, 1)
        for limit in (0, -1):
            with self.assertRaisesMessage(CommandError, '양의 정수'):
                self.run_command(limit=limit)


class ReminderMigrationTests(TestCase):
    def test_schedule_fk_and_migration_dependencies_are_explicit(self):
        field = DeadlineReminder._meta.get_field('schedule')
        self.assertEqual(field.related_model, Schedule)
        self.assertEqual(field.remote_field.related_name, 'deadline_reminders')
        loader = MigrationLoader(connection)
        migration = loader.disk_migrations[('notifications', '0002_schedule_deadline_reminders')]
        self.assertIn(('documents', '0003_schedule'), migration.dependencies)
        self.assertIn(('notifications', '0001_initial'), migration.dependencies)
