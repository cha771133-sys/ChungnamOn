from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from documents.models import Document
from .helpers import create_user


class AuthIntegrationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user('owner')
        cls.other = create_user('other')
        cls.document = Document.objects.create(
            owner=cls.owner, file='notice.pdf', file_name='notice.pdf', file_type='PDF', step='QUEUED',
        )

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        body = response.json()
        self.assertEqual(set(body), {'code', 'message', 'details'})
        self.assertEqual(body['code'], code)
        self.assertTrue(body['message'])
        if status == 401:
            self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="api"')
            self.assertEqual(body['details'], {})
        return body

    def test_real_protected_routes_share_missing_and_invalid_jwt_errors(self):
        for header, code in (('', 'NOT_AUTHENTICATED'), ('Bearer invalid-private-token', 'AUTHENTICATION_FAILED')):
            self.client.credentials(HTTP_AUTHORIZATION=header)
            for method, path, data in (
                ('post', '/api/documents/', {}),
                ('get', f'/api/documents/{self.document.pk}/status/', None),
                ('get', f'/api/documents/{self.document.pk}/result/', None),
                ('get', '/api/auth/me/', None),
                ('patch', '/api/auth/me/', {}),
            ):
                with self.subTest(header=bool(header), method=method, path=path):
                    response = getattr(self.client, method)(path, data, format='json')
                    self.assert_error(response, 401, code)
                    self.assertNotIn('invalid-private-token', str(response.json()))

    def test_auth_validation_retains_field_details_and_no_partial_writes(self):
        response = self.client.post('/api/auth/register/', {}, format='json')
        body = self.assert_error(response, 400, 'VALIDATION_ERROR')
        self.assertEqual(set(body['details']), {'email', 'password'})
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.owner)))
        response = self.client.patch('/api/auth/me/', {'language': 'en'}, format='json')
        body = self.assert_error(response, 400, 'VALIDATION_ERROR')
        self.assertEqual(set(body['details']), {'nationality', 'visaType'})
        self.assertFalse(hasattr(type(self.owner).objects.get(pk=self.owner.pk), 'profile'))

    def test_invalid_refresh_and_logout_use_common_401_errors(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer stale-access')
        for path in ('/api/auth/token/refresh/', '/api/auth/logout/'):
            with self.subTest(path=path):
                response = self.client.post(path, {'refresh': 'invalid-private-refresh'}, format='json')
                self.assert_error(response, 401, 'AUTHENTICATION_FAILED')
                self.assertNotIn('invalid-private-refresh', str(response.json()))
                response = self.client.post(path, {}, format='json')
                body = self.assert_error(response, 400, 'VALIDATION_ERROR')
                self.assertIn('refresh', body['details'])

    def test_result_owner_filter_hides_foreign_and_missing_documents(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.other)))
        foreign = self.client.get(f'/api/documents/{self.document.pk}/result/')
        missing = self.client.get('/api/documents/999999/result/')
        self.assert_error(foreign, 404, 'NOT_FOUND')
        self.assert_error(missing, 404, 'NOT_FOUND')
        self.assertEqual(foreign.json(), missing.json())

    @override_settings(DOCUMENTS_PROCESS_SYNC=True, DOCUMENTS_STEP_DELAY=0)
    def test_registration_profile_document_rotation_and_logout_flow(self):
        # 공개 인증 API는 만료된 Access 헤더와 무관하게 본문의 인증 정보를 사용한다.
        self.client.credentials(HTTP_AUTHORIZATION='Bearer stale-access')
        credentials = {'email': 'new@example.com', 'password': 'Bamboo!River8042'}
        registered = self.client.post('/api/auth/register/', credentials, format='json')
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(set(registered.json()), {'id', 'email'})
        login = self.client.post('/api/auth/login/', credentials, format='json')
        self.assertEqual(login.status_code, 200)
        tokens = login.json()
        self.assertEqual(set(tokens), {'access', 'refresh'})
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens['access'])
        profile = self.client.get('/api/auth/me/')
        self.assertEqual(profile.status_code, 200)
        self.assertFalse(profile.json()['onboardingCompleted'])
        updated = self.client.patch('/api/auth/me/', {
            'language': 'vi', 'nationality': 'VN', 'visaType': 'E-9',
        }, format='json')
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json()['onboardingCompleted'])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with self.captureOnCommitCallbacks(execute=True):
                uploaded = self.client.post('/api/documents/', {
                    'file': SimpleUploadedFile('notice.pdf', b'pdf'), 'owner': self.other.pk,
                }, format='multipart')
            self.assertEqual(uploaded.status_code, 201)
            document = Document.objects.get(pk=uploaded.json()['documentId'])
            self.assertEqual(uploaded.json(), {'documentId': document.pk, 'status': 'PENDING'})
            self.assertEqual(document.owner_id, registered.json()['id'])
            status = self.client.get(f'/api/documents/{document.pk}/status/')
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json(), {'documentId': document.pk, 'status': 'DONE', 'step': 'DONE'})
            result = self.client.get(f'/api/documents/{document.pk}/result/')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['documentId'], document.pk)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer stale-access')
        rotated = self.client.post('/api/auth/token/refresh/', {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.json()['refresh'], tokens['refresh'])
        old = self.client.post('/api/auth/token/refresh/', {'refresh': tokens['refresh']}, format='json')
        self.assert_error(old, 401, 'AUTHENTICATION_FAILED')
        logout = self.client.post('/api/auth/logout/', {'refresh': rotated.json()['refresh']}, format='json')
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json(), {})
        revoked = self.client.post('/api/auth/token/refresh/', {'refresh': rotated.json()['refresh']}, format='json')
        self.assert_error(revoked, 401, 'AUTHENTICATION_FAILED')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + rotated.json()['access'])
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
