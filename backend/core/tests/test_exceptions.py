from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.test import SimpleTestCase
from rest_framework import serializers
from rest_framework.exceptions import (
    APIException, AuthenticationFailed, MethodNotAllowed, NotAuthenticated,
    NotFound, ParseError, PermissionDenied, Throttled, UnsupportedMediaType,
    ValidationError,
)
from rest_framework.test import APIRequestFactory
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.exceptions import api_exception_handler


class ChildSerializer(serializers.Serializer):
    value = serializers.IntegerField()


class NestedSerializer(serializers.Serializer):
    child = ChildSerializer()
    items = ChildSerializer(many=True)


class ExceptionHandlerTests(SimpleTestCase):
    def test_handled_errors_keep_status_and_use_standard_contract(self):
        for exc, status, code in (
            (NotAuthenticated(), 401, 'NOT_AUTHENTICATED'),
            (AuthenticationFailed(), 401, 'AUTHENTICATION_FAILED'),
            (PermissionDenied(), 403, 'PERMISSION_DENIED'),
            (DjangoPermissionDenied(), 403, 'PERMISSION_DENIED'),
            (NotFound(), 404, 'NOT_FOUND'),
            (Http404(), 404, 'NOT_FOUND'),
            (MethodNotAllowed('DELETE'), 405, 'METHOD_NOT_ALLOWED'),
            (UnsupportedMediaType('text/plain'), 415, 'UNSUPPORTED_MEDIA_TYPE'),
            (Throttled(wait=12), 429, 'THROTTLED'),
            (ParseError(), 400, 'PARSE_ERROR'),
        ):
            with self.subTest(code=code, exception=type(exc).__name__):
                response = api_exception_handler(exc, {})
                self.assertEqual(response.status_code, status)
                self.assertEqual(set(response.data), {'code', 'message', 'details'})
                self.assertEqual(response.data['code'], code)
                self.assertTrue(response.data['message'])
                self.assertEqual(response.data['details'], {})

    def test_nested_serializer_errors_are_preserved_without_flattening(self):
        serializer = NestedSerializer(data={'child': {}, 'items': [{'value': 'bad'}, {}]})
        self.assertFalse(serializer.is_valid())
        response = api_exception_handler(ValidationError(serializer.errors), {})
        self.assertEqual(response.data['code'], 'VALIDATION_ERROR')
        self.assertEqual(response.data['details'], serializer.errors)
        self.assertEqual(len(response.data['details']['items']), 2)
        self.assertIn('value', response.data['details']['child'])

    def test_non_field_validation_errors_are_preserved(self):
        response = api_exception_handler(ValidationError(['첫 오류', '두 번째 오류']), {})
        self.assertEqual(response.data['details'], {'non_field_errors': ['첫 오류', '두 번째 오류']})

    def test_authentication_and_internal_exception_details_are_not_exposed(self):
        secret = 'password=hidden token=hidden C:/private/server.py SELECT * FROM users Traceback'
        for exc in (AuthenticationFailed(secret), PermissionDenied(secret), Http404(secret),
                    APIException(secret)):
            with self.subTest(exc=type(exc).__name__):
                response = api_exception_handler(exc, {})
                self.assertNotIn(secret, str(response.data))
                self.assertEqual(response.data['details'], {})
        self.assertIsNone(api_exception_handler(RuntimeError(secret), {}))

    def test_authenticate_and_retry_headers_are_preserved(self):
        exc = AuthenticationFailed()
        exc.auth_header = 'Bearer realm="api"'
        self.assertEqual(api_exception_handler(exc, {})['WWW-Authenticate'], exc.auth_header)
        self.assertEqual(api_exception_handler(Throttled(wait=12), {})['Retry-After'], '12')

    def test_existing_session_authentication_403_policy_is_respected(self):
        response = APIView.as_view(permission_classes=[IsAuthenticated])(APIRequestFactory().get('/'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'NOT_AUTHENTICATED')
