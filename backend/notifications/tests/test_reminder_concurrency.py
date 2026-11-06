from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from documents.models import Document, Schedule
from notifications.models import DeadlineReminder, NotificationPreference, ReminderDelivery
from notifications.services.devices import register_device
from notifications.services.push_sender import PushResult, PushStatus
from .helpers import FID
from .test_command import BEFORE_DUE, COMMAND, NOW, TODAY


class ConcurrentReminderTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_concurrent_commands_send_one_success_per_device_event(self):
        """독립 PostgreSQL 연결에서 실행한 두 명령이 같은 이벤트를 중복 발송하지 않는다."""
        user = get_user_model().objects.create_user(email='parallel-reminder@example.com')
        document = Document.objects.create(
            owner=user, file='documents/concurrent.pdf', file_name='concurrent.pdf', file_type='PDF',
        )
        schedule = Schedule.objects.create(owner=user, document=document, date=TODAY.replace(day=27), title='동시 실행')
        Schedule.objects.filter(pk=schedule.pk).update(created_at=BEFORE_DUE)
        NotificationPreference.objects.create(user=user, enabled=True, enabled_since=BEFORE_DUE)
        register_device(user=user, registration_id=FID, registration_type='FID', platform='WEB')
        barrier = Barrier(2)

        def execute():
            """각 worker가 독립 연결로 명령을 실행하고 연결을 정리한다."""
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                call_command('send_deadline_reminders', stdout=StringIO())
            finally:
                close_old_connections()

        accepted = PushResult(PushStatus.ACCEPTED, provider_message_id='projects/test/messages/concurrent')
        with patch('django.utils.timezone.now', return_value=NOW), \
                patch(f'{COMMAND}.send_deadline_push', return_value=accepted) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(execute) for _ in range(2)]
                for future in futures:
                    future.result(timeout=20)
        send.assert_called_once()
        self.assertEqual(DeadlineReminder.objects.count(), 1)
        self.assertEqual(ReminderDelivery.objects.get().status, ReminderDelivery.Status.SENT)
