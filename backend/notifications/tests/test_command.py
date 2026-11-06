from datetime import date, datetime, timedelta, timezone as datetime_timezone
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import RequestFactory, TestCase
from django.utils import timezone

from documents.models import Document, Schedule
from notifications.models import DeadlineReminder, NotificationPreference, ReminderDelivery
from notifications.services.devices import register_device
from notifications.services.push_sender import PushResult, PushStatus
from notifications.services.reminder_service import claim_delivery
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

    def run_command(self, *, now=NOW, expected_error=None, **options):
        """명령이 실제 한국 날짜 계산과 실패 종료를 함께 사용하도록 호출한다."""
        output = StringIO()
        with patch('django.utils.timezone.now', return_value=now):
            if expected_error:
                with self.assertRaisesMessage(CommandError, expected_error):
                    call_command('send_deadline_reminders', stdout=output, **options)
            else:
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
            output = self.run_command(expected_error='FIREBASE_CONFIGURATION_ERROR')

        delivery = ReminderDelivery.objects.get(reminder__schedule=schedule)
        self.assertEqual(delivery.status, ReminderDelivery.Status.CONFIGURATION_ERROR)
        self.assertFalse(delivery.provider_message_id)
        self.assertIn('accepted=0', output)
        self.assertIn('configuration_errors=1', output)

    def test_claim_prevents_a_second_worker_from_sending_the_same_device_event(self):
        """PROCESSING 상태는 결과 불명 재발송보다 중복 전송 방지를 우선한다."""
        schedule = self.create_schedule(TODAY.replace(day=27))
        with patch('django.utils.timezone.localdate', return_value=TODAY), \
                patch('django.utils.timezone.now', return_value=NOW):
            first = claim_delivery(schedule_id=schedule.pk, days_before=7, device_id=self.device.pk)
            second = claim_delivery(schedule_id=schedule.pk, days_before=7, device_id=self.device.pk)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(ReminderDelivery.objects.get().status, ReminderDelivery.Status.PROCESSING)

    def test_configuration_failure_stops_remaining_devices_and_events_then_allows_recovery(self):
        self.create_schedule(TODAY + timedelta(days=1))
        self.create_schedule(TODAY + timedelta(days=7))
        register_device(user=self.user, registration_id=OTHER_FID, registration_type='FID', platform='WEB')
        failure = PushResult(PushStatus.CONFIGURATION_ERROR, 'FIREBASE_CONFIGURATION')
        with patch(f'{COMMAND}.send_deadline_push', return_value=failure) as send:
            self.run_command(expected_error='FIREBASE_CONFIGURATION_ERROR')
        send.assert_called_once()
        self.assertEqual(ReminderDelivery.objects.count(), 1)
        self.assertEqual(DeadlineReminder.objects.count(), 1)

        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/recovered')
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.run_command()
        self.assertEqual(send.call_count, 4)
        self.assertEqual(ReminderDelivery.objects.filter(status=ReminderDelivery.Status.SENT).count(), 4)

    def test_quota_rejection_retries_same_delivery_on_same_day_and_never_resends_success(self):
        self.create_schedule(TODAY + timedelta(days=7))
        failure = PushResult(PushStatus.RETRYABLE, 'QUOTA_EXCEEDED')
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/retry')
        with patch(f'{COMMAND}.send_deadline_push', return_value=failure):
            self.run_command(expected_error='DEADLINE_REMINDER_FAILURE')
        delivery = ReminderDelivery.objects.get()
        self.assertEqual(delivery.status, ReminderDelivery.Status.RETRYABLE)
        self.assertFalse(delivery.provider_message_id)
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.run_command(now=NOW + timedelta(hours=1))
            self.run_command(now=NOW + timedelta(hours=2))
        send.assert_called_once()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ReminderDelivery.Status.SENT)
        self.assertEqual(delivery.provider_message_id, accepted.provider_message_id)
        self.assertFalse(delivery.error_code)
        self.assertEqual(ReminderDelivery.objects.count(), 1)

    def test_quota_rejection_is_not_retried_on_next_korean_day(self):
        self.create_schedule(TODAY + timedelta(days=7))
        failure = PushResult(PushStatus.RETRYABLE, 'QUOTA_EXCEEDED')
        with patch(f'{COMMAND}.send_deadline_push', return_value=failure) as send:
            self.run_command(expected_error='DEADLINE_REMINDER_FAILURE')
            self.run_command(now=NOW + timedelta(days=1))
        send.assert_called_once()
        self.assertEqual(ReminderDelivery.objects.get().status, ReminderDelivery.Status.RETRYABLE)

    def test_uncertain_and_permanent_failures_never_retry_or_disable_device(self):
        self.create_schedule(TODAY + timedelta(days=7))
        for result, status in (
            (PushResult(PushStatus.UNKNOWN, 'NETWORK_RESULT_UNKNOWN'), ReminderDelivery.Status.UNKNOWN),
            (PushResult(PushStatus.PERMANENT_ERROR, 'INVALID_ARGUMENT'), ReminderDelivery.Status.FAILED),
        ):
            with self.subTest(status=status):
                ReminderDelivery.objects.all().delete()
                with patch(f'{COMMAND}.send_deadline_push', return_value=result) as send:
                    self.run_command(expected_error='DEADLINE_REMINDER_FAILURE')
                    self.run_command()
                send.assert_called_once()
                self.assertEqual(ReminderDelivery.objects.get().status, status)
                self.device.refresh_from_db()
                self.assertTrue(self.device.is_active)
                self.assertIsNone(self.device.last_success_at)

    def test_done_toggles_preserve_success_and_undone_unsent_schedule_is_reevaluated(self):
        from rest_framework.test import APIClient

        schedule = self.create_schedule(TODAY + timedelta(days=7))
        client = APIClient()
        client.force_authenticate(self.user)
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/toggle')
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.assertEqual(client.patch(f'/api/schedules/{schedule.pk}/', {'done': True}).status_code, 200)
            self.run_command()
            send.assert_not_called()
            self.assertEqual(client.patch(f'/api/schedules/{schedule.pk}/', {'done': False}).status_code, 200)
            self.run_command()
            self.assertEqual(client.patch(f'/api/schedules/{schedule.pk}/', {'done': True}).status_code, 200)
            self.assertEqual(client.patch(f'/api/schedules/{schedule.pk}/', {'done': False}).status_code, 200)
            self.run_command()
        send.assert_called_once()
        self.assertEqual(ReminderDelivery.objects.get().status, ReminderDelivery.Status.SENT)

    def test_same_schedule_d7_success_does_not_block_d1(self):
        schedule = self.create_schedule(TODAY + timedelta(days=7))
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/event')
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            self.run_command()
            self.run_command(now=NOW + timedelta(days=6))
        self.assertEqual(send.call_count, 2)
        self.assertEqual({call.kwargs['schedule_id'] for call in send.call_args_list}, {schedule.pk})
        self.assertEqual(set(schedule.deadline_reminders.values_list('days_before', flat=True)), {7, 1})

    def test_korean_midnight_prevents_previous_day_event_and_utc_midnight_is_nine_am(self):
        self.create_schedule(TODAY + timedelta(days=7))
        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/boundary')
        with patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            # UTC 8/19 15:00 = 한국 8/20 00:00: 아직 09:00 전이다.
            self.run_command(now=datetime(2026, 8, 19, 15, tzinfo=datetime_timezone.utc))
            send.assert_not_called()
            self.run_command(now=datetime(2026, 8, 20, 0, tzinfo=datetime_timezone.utc))
            send.assert_called_once()
            self.run_command(now=datetime(2026, 8, 20, 15, tzinfo=datetime_timezone.utc))
        send.assert_called_once()

    def test_completion_or_deletion_after_claim_prevents_external_call(self):
        for change in ('done', 'delete'):
            with self.subTest(change=change):
                schedule = self.create_schedule(TODAY + timedelta(days=7), title=change)

                def claim_then_change(**kwargs):
                    claim = claim_delivery(**kwargs)
                    if change == 'done':
                        Schedule.objects.filter(pk=schedule.pk).update(done=True)
                    else:
                        Schedule.objects.filter(pk=schedule.pk).delete()
                    return claim

                with patch(f'{COMMAND}.claim_delivery', side_effect=claim_then_change), \
                        patch(f'{COMMAND}.send_deadline_push') as send:
                    self.run_command()
                send.assert_not_called()
        self.assertFalse(ReminderDelivery.objects.filter(status=ReminderDelivery.Status.PROCESSING).exists())

    def test_device_transfer_or_consent_revocation_after_claim_prevents_external_call(self):
        self.create_schedule(TODAY + timedelta(days=7))

        def claim_then_revoke(**kwargs):
            claim = claim_delivery(**kwargs)
            NotificationPreference.objects.filter(pk=self.preference.pk).update(enabled=False, enabled_since=None)
            return claim

        with patch(f'{COMMAND}.claim_delivery', side_effect=claim_then_revoke), \
                patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        self.assertEqual(ReminderDelivery.objects.get().status, ReminderDelivery.Status.PENDING)

        other = get_user_model().objects.create_user(email='device-new-owner@example.com')
        NotificationPreference.objects.filter(pk=self.preference.pk).update(enabled=True, enabled_since=BEFORE_DUE)

        def claim_then_transfer(**kwargs):
            claim = claim_delivery(**kwargs)
            register_device(user=other, registration_id=FID, registration_type='FID', platform='WEB')
            return claim

        with patch(f'{COMMAND}.claim_delivery', side_effect=claim_then_transfer), \
                patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        self.device.refresh_from_db()
        self.assertEqual(self.device.user, other)

    def test_deletion_during_external_call_does_not_recreate_cascaded_history(self):
        schedule = self.create_schedule(TODAY + timedelta(days=7))

        def accepted_while_deleted(**kwargs):
            Schedule.objects.filter(pk=schedule.pk).delete()
            return PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/deleted')

        with patch(f'{COMMAND}.send_deadline_push', side_effect=accepted_while_deleted):
            self.run_command()
        self.assertFalse(DeadlineReminder.objects.exists())
        self.assertFalse(ReminderDelivery.objects.exists())

    def test_owner_mismatch_revoked_consent_and_inactive_devices_are_excluded(self):
        schedule = self.create_schedule(TODAY + timedelta(days=7))
        other = get_user_model().objects.create_user(email='changed-document-owner@example.com')
        Document.objects.filter(pk=self.document.pk).update(owner=other)
        with patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        Document.objects.filter(pk=self.document.pk).update(owner=self.user)
        NotificationPreference.objects.filter(pk=self.preference.pk).update(enabled=False, enabled_since=None)
        with patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        NotificationPreference.objects.filter(pk=self.preference.pk).update(enabled=True, enabled_since=BEFORE_DUE)
        self.device.is_active = False
        self.device.save()
        with patch(f'{COMMAND}.send_deadline_push') as send:
            self.run_command()
        send.assert_not_called()
        self.assertFalse(ReminderDelivery.objects.filter(reminder__schedule=schedule).exists())

    def test_sdk_payload_and_lock_screen_never_include_sensitive_titles(self):
        schedule = self.create_schedule(TODAY + timedelta(days=7))
        with patch('notifications.services.push_sender.get_firebase_app'), \
                patch('notifications.services.push_sender.messaging.send', return_value='projects/test/messages/payload') as send:
            output = self.run_command()
        message = send.call_args.args[0]
        self.assertEqual(message.data['scheduleId'], str(schedule.pk))
        self.assertEqual(message.data['documentId'], str(self.document.pk))
        self.assertTrue(all(isinstance(value, str) for value in message.data.values()))
        lock_screen = message.notification.title + message.notification.body
        for private in (schedule.title, self.document.title, FID):
            self.assertNotIn(private, lock_screen)
            self.assertNotIn(private, output)

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
        reminder = DeadlineReminder.objects.create(schedule=schedule, days_before=7)
        ReminderDelivery.objects.create(reminder=reminder, device=self.device)
        self.user.delete()
        self.assertFalse(DeadlineReminder.objects.exists())
        self.assertFalse(ReminderDelivery.objects.exists())

    def test_reminder_history_does_not_block_existing_schedule_and_document_admin_deletion(self):
        """알림 이력이 기존 Schedule/Document 관리자 삭제 권한에 제약을 추가하지 않는다."""
        schedule = self.create_schedule(TODAY + timedelta(days=7))
        reminder = DeadlineReminder.objects.create(schedule=schedule, days_before=7)
        ReminderDelivery.objects.create(reminder=reminder, device=self.device)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        request = RequestFactory().get('/admin/documents/')
        request.user = self.user
        for model, instance in ((Schedule, schedule), (Document, self.document)):
            with self.subTest(model=model.__name__):
                _, _, permissions, protected = admin.site._registry[model].get_deleted_objects([instance], request)
                self.assertFalse(permissions)
                self.assertFalse(protected)
        self.client.force_login(self.user)
        response = self.client.post(f'/admin/documents/document/{self.document.pk}/delete/', {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Schedule.objects.filter(pk=schedule.pk).exists())
        self.assertFalse(DeadlineReminder.objects.exists())
        self.assertFalse(ReminderDelivery.objects.exists())

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
