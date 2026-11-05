from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase


class BlockedCommandTests(SimpleTestCase):
    def test_no_fake_success_without_schedule_integration(self):
        for options in ({}, {'dry_run': True}, {'limit': 1}, {'dry_run': True, 'limit': 10}):
            with self.subTest(options=options), patch('notifications.services.push_sender.messaging.send') as send:
                output = StringIO()
                with self.assertRaisesMessage(CommandError, 'SCHEDULE_INTEGRATION_UNAVAILABLE'):
                    call_command('send_deadline_reminders', stdout=output, **options)
                self.assertEqual(output.getvalue(), '')
                send.assert_not_called()

    def test_invalid_limit_is_rejected(self):
        for limit in (0, -1):
            with self.assertRaisesMessage(CommandError, '양의 정수'):
                call_command('send_deadline_reminders', limit=limit)
