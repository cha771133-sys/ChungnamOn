from datetime import date
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings
from firebase_admin import exceptions, messaging
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from notifications.services.push_sender import (
    APP_NAME, PushStatus, build_deadline_message, get_firebase_app, send_deadline_push,
)
from .helpers import FID

MODULE = 'notifications.services.push_sender'


class PushSenderTests(SimpleTestCase):
    def fields(self, **overrides):
        return {'registration_id': FID, 'schedule_id': 123, 'document_id': 456,
                'deadline_date': date(2026, 11, 20), 'days_before': 7, **overrides}

    def test_message_uses_fid_generic_task_deadline_and_db_identifiers(self):
        message = build_deadline_message(**self.fields())
        self.assertEqual(message.fid, FID)
        self.assertIsNone(message.token)
        self.assertIn('7일', message.notification.title)
        self.assertEqual(message.notification.body, '등록한 행정업무의 마감일은 11월 20일입니다.')
        self.assertEqual(message.data, {
            'type': 'DEADLINE_REMINDER', 'scheduleId': '123', 'documentId': '456',
            'daysBefore': '7', 'deadlineDate': '2026-11-20', 'url': '/?schedule=123',
        })
        self.assertNotIn(FID, str(message.data))

    def test_optional_document_and_relative_deeplink(self):
        message = build_deadline_message(**self.fields(document_id=None, schedule_id='id&next=https://example.com'))
        self.assertNotIn('documentId', message.data)
        self.assertEqual(message.data['url'], '/?schedule=id%26next%3Dhttps%3A%2F%2Fexample.com')

    def test_invalid_payload_never_initializes_firebase(self):
        """잘못된 내부 입력과 원문 업무명 전달이 외부 인증·전송 전에 거부되는지 확인한다."""
        for fields in (self.fields(days_before=3), self.fields(days_before=True),
                       self.fields(deadline_date=None), self.fields(task_name='개인정보')):
            with self.subTest(fields=list(fields)), patch(f'{MODULE}.get_firebase_app') as initialize:
                result = send_deadline_push(**fields)
                self.assertEqual(result.status, PushStatus.PERMANENT_ERROR)
                initialize.assert_not_called()

    @patch(f'{MODULE}.messaging.send', return_value='projects/test/messages/123')
    @patch(f'{MODULE}.get_firebase_app')
    def test_success_is_provider_acceptance(self, initialize, send):
        result = send_deadline_push(**self.fields())
        self.assertEqual(result.status, PushStatus.ACCEPTED)
        self.assertEqual(result.provider_message_id, 'projects/test/messages/123')
        send.assert_called_once()
        self.assertIs(send.call_args.kwargs['app'], initialize.return_value)

    def test_error_classification_no_raw_error_text_or_application_retry(self):
        """공급자 오류별 안전한 분류와 원문 비노출·추가 재시도 없음을 검증한다."""
        cases = [
            (messaging.UnregisteredError(FID), PushStatus.INVALID_DEVICE, 'UNREGISTERED'),
            (messaging.SenderIdMismatchError(FID), PushStatus.CONFIGURATION_ERROR, 'FIREBASE_AUTHENTICATION'),
            (messaging.ThirdPartyAuthError(FID), PushStatus.CONFIGURATION_ERROR, 'FIREBASE_AUTHENTICATION'),
            (exceptions.UnauthenticatedError(FID), PushStatus.CONFIGURATION_ERROR, 'FIREBASE_AUTHENTICATION'),
            (RefreshError(FID), PushStatus.CONFIGURATION_ERROR, 'FIREBASE_AUTHENTICATION'),
            (exceptions.InvalidArgumentError(FID), PushStatus.PERMANENT_ERROR, 'INVALID_ARGUMENT'),
            (messaging.QuotaExceededError(FID), PushStatus.RETRYABLE, 'QUOTA_EXCEEDED'),
            (exceptions.UnavailableError(FID), PushStatus.UNKNOWN, 'SERVICE_UNAVAILABLE'),
            (exceptions.InternalError(FID), PushStatus.UNKNOWN, 'SERVICE_UNAVAILABLE'),
            (exceptions.DeadlineExceededError(FID), PushStatus.UNKNOWN, 'NETWORK_RESULT_UNKNOWN'),
            (requests.Timeout(FID), PushStatus.UNKNOWN, 'NETWORK_RESULT_UNKNOWN'),
            (RuntimeError(FID), PushStatus.UNKNOWN, 'UNEXPECTED_PROVIDER_ERROR'),
        ]
        for error, status, code in cases:
            with self.subTest(error=type(error).__name__), patch(f'{MODULE}.get_firebase_app'), \
                    patch(f'{MODULE}.messaging.send', side_effect=error) as send:
                result = send_deadline_push(**self.fields())
                self.assertEqual(result.status, status)
                self.assertEqual(result.error_code, code)
                self.assertNotIn(FID, repr(result))
                self.assertFalse(result.provider_message_id)
                send.assert_called_once()

    @patch(f'{MODULE}.get_firebase_app', side_effect=DefaultCredentialsError(FID))
    @patch(f'{MODULE}.messaging.send')
    def test_configuration_failure_never_sends(self, send, initialize):
        result = send_deadline_push(**self.fields())
        self.assertEqual(result.status, PushStatus.CONFIGURATION_ERROR)
        self.assertNotIn(FID, repr(result))
        send.assert_not_called()

    @patch(f'{MODULE}.get_firebase_app')
    @patch(f'{MODULE}.messaging.send', return_value='')
    def test_empty_provider_response_is_uncertain(self, send, initialize):
        self.assertEqual(send_deadline_push(**self.fields()).status, PushStatus.UNKNOWN)


class FirebaseInitializationTests(SimpleTestCase):
    @override_settings(FIREBASE_SERVICE_ACCOUNT_JSON='{"project_id":"test-project"}')
    @patch(f'{MODULE}.credentials.Certificate')
    @patch(f'{MODULE}.firebase_admin.initialize_app')
    @patch(f'{MODULE}.firebase_admin.get_app')
    def test_named_app_is_reused(self, get_app, initialize, certificate):
        app = Mock()
        get_app.side_effect = [ValueError(), app]
        initialize.return_value = app
        self.assertIs(get_firebase_app(), app)
        self.assertIs(get_firebase_app(), app)
        certificate.assert_called_once_with({'project_id': 'test-project'})
        initialize.assert_called_once_with(certificate.return_value, {'httpTimeout': 10}, name=APP_NAME)

    @override_settings(FIREBASE_SERVICE_ACCOUNT_JSON='')
    @patch(f'{MODULE}.credentials.ApplicationDefault')
    @patch(f'{MODULE}.firebase_admin.initialize_app')
    @patch(f'{MODULE}.firebase_admin.get_app', side_effect=ValueError())
    def test_adc_is_only_requested_when_sending(self, get_app, initialize, adc):
        get_firebase_app()
        adc.assert_called_once_with()
        initialize.assert_called_once()

    @override_settings(FIREBASE_SERVICE_ACCOUNT_JSON='invalid-json')
    @patch(f'{MODULE}.firebase_admin.get_app', side_effect=ValueError())
    @patch(f'{MODULE}.messaging.send')
    def test_invalid_json_is_safe_configuration_error(self, send, get_app):
        result = send_deadline_push(registration_id=FID, schedule_id=1,
                                    deadline_date=date(2026, 11, 20), days_before=1)
        self.assertEqual(result.status, PushStatus.CONFIGURATION_ERROR)
        send.assert_not_called()
