from datetime import date, datetime, timedelta, timezone

from django.test import SimpleTestCase

from notifications.services.reminder_service import KOREA, is_due_today, reminder_time


class ReminderTimeTests(SimpleTestCase):
    def test_d7_and_d1_are_korean_calendar_dates_at_nine(self):
        for days, day in ((7, 13), (1, 19)):
            with self.subTest(days=days):
                result = reminder_time(date(2026, 11, 20), days)
                self.assertEqual(result, datetime(2026, 11, day, 9, tzinfo=KOREA))
                self.assertEqual(result.astimezone(timezone.utc).hour, 0)

    def test_month_year_and_leap_boundaries(self):
        self.assertEqual(reminder_time(date(2028, 3, 1), 1).date(), date(2028, 2, 29))
        self.assertEqual(reminder_time(date(2027, 1, 1), 7).date(), date(2026, 12, 25))

    def due(self, **overrides):
        scheduled = reminder_time(date(2026, 11, 20), 7)
        return is_due_today(**{
            'scheduled_at': scheduled, 'now': scheduled, 'created_at': scheduled - timedelta(days=1),
            'enabled_since': scheduled - timedelta(days=1), **overrides,
        })

    def test_only_same_korean_day_late_execution_is_due(self):
        self.assertTrue(self.due())
        self.assertTrue(self.due(now=datetime(2026, 11, 13, 23, 59, tzinfo=KOREA)))
        self.assertFalse(self.due(now=datetime(2026, 11, 14, 0, 0, tzinfo=KOREA)))
        self.assertFalse(self.due(now=datetime(2026, 11, 13, 8, 59, tzinfo=KOREA)))

    def test_utc_previous_day_and_korean_midnight(self):
        self.assertFalse(self.due(now=datetime(2026, 11, 12, 23, 59, tzinfo=timezone.utc)))
        self.assertTrue(self.due(now=datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)))
        self.assertFalse(self.due(now=datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)))

    def test_late_created_schedule_or_reenable_is_not_retroactive(self):
        later = datetime(2026, 11, 13, 10, tzinfo=KOREA)
        self.assertFalse(self.due(now=later, created_at=later))
        self.assertFalse(self.due(now=later, enabled_since=later))
        self.assertFalse(self.due(enabled_since=None))

    def test_d7_can_be_past_while_d1_still_due(self):
        created = datetime(2026, 11, 15, 10, tzinfo=KOREA)
        current = datetime(2026, 11, 19, 10, tzinfo=KOREA)
        self.assertTrue(self.due(scheduled_at=reminder_time(date(2026, 11, 20), 1),
                                 now=current, created_at=created))
        self.assertFalse(self.due(now=current, created_at=created))

    def test_invalid_or_naive_times_are_rejected(self):
        with self.assertRaises(ValueError):
            self.due(now=datetime(2026, 11, 13))
        for deadline, days in ((None, 1), (date(2026, 11, 20), 2), (date(2026, 11, 20), True)):
            with self.assertRaises(ValueError):
                reminder_time(deadline, days)
