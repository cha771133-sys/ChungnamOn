from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from documents.models import Document

User = get_user_model()
PASSWORD = 'Bamboo!River8042'
REGISTER = '/api/auth/register/'
LOGIN = '/api/auth/login/'
REFRESH = '/api/auth/token/refresh/'
LOGOUT = '/api/auth/logout/'


class RegistrationTests(APITestCase):
    def register(self, email='person@example.com', password=PASSWORD, **extra):
        return self.client.post(REGISTER, {'email': email, 'password': password, **extra}, format='json')

    def test_register_returns_only_public_fields_and_hashes_password(self):
        response = self.register('  Person@EXAMPLE.COM ')
        self.assertEqual(response.status_code, 201)
        user = User.objects.get()
        self.assertEqual(response.json(), {'id': user.id, 'email': 'person@example.com'})
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotIn(PASSWORD, response.content.decode())
        self.assertNotIn(user.password, response.content.decode())
        self.assertFalse(hasattr(user, 'profile'))

    def test_duplicate_and_case_variant_email(self):
        User.objects.create_user('person@example.com', PASSWORD)
        for email in ('person@example.com', ' PERSON@EXAMPLE.COM '):
            with self.subTest(email=email):
                response = self.register(email)
                self.assertEqual(response.status_code, 400)
                self.assertIn('email', response.json()['details'])
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_and_missing_email(self):
        for email in ('', '   ', 'invalid', None):
            with self.subTest(email=email):
                response = self.register(email)
                self.assertEqual(response.status_code, 400)
                self.assertIn('email', response.json()['details'])
        self.assertEqual(self.client.post(REGISTER, {'password': PASSWORD}).status_code, 400)

    def test_password_validators_and_missing_password(self):
        for password in ('short', 'password', '1234567891011', 'person@example.com', '', None):
            with self.subTest(password=password):
                response = self.register(password=password)
                self.assertEqual(response.status_code, 400)
                self.assertIn('password', response.json()['details'])
        self.assertEqual(self.client.post(REGISTER, {'email': 'person@example.com'}).status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_registration_rejects_privilege_fields(self):
        response = self.register(is_staff=True, is_superuser=True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_duplicate_registration_race_is_a_validation_error(self):
        User.objects.create_user('person@example.com', PASSWORD)
        with patch('accounts.serializers.RegistrationSerializer.validate_email', return_value='person@example.com'):
            response = self.register()
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.json()['details'])


class JWTTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('person@example.com', PASSWORD)
        cls.document = Document.objects.create(
            owner=cls.user, file='documents/notice.pdf', file_name='notice.pdf', file_type='PDF',
        )

    @property
    def protected_url(self):
        return f'/api/documents/{self.document.id}/status/'

    def login(self, email='person@example.com', password=PASSWORD):
        return self.client.post(LOGIN, {'email': email, 'password': password}, format='json')

    def refresh(self, token):
        return self.client.post(REFRESH, {'refresh': token}, format='json')

    def logout(self, token):
        return self.client.post(LOGOUT, {'refresh': token}, format='json')

    def test_login_normalizes_email_and_sets_token_lifetimes(self):
        response = self.login('  PERSON@EXAMPLE.COM ')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'access', 'refresh'})
        access = AccessToken(response.json()['access'])
        refresh = RefreshToken(response.json()['refresh'])
        self.assertEqual(access['exp'] - access['iat'], 15 * 60)
        self.assertEqual(refresh['exp'] - refresh['iat'], 7 * 24 * 60 * 60)

    def test_password_whitespace_is_preserved(self):
        password = '  Bamboo!River8042  '
        response = self.client.post(REGISTER, {
            'email': 'spaces@example.com', 'password': password,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.login('spaces@example.com', password).status_code, 200)
        self.assertEqual(self.login('spaces@example.com', password.strip()).status_code, 401)

    def test_wrong_password_and_unknown_account_have_same_failure(self):
        wrong = self.login(password='incorrect')
        missing = self.login(email='missing@example.com')
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.json(), missing.json())
        self.assertNotIn('incorrect', wrong.content.decode())

    def test_inactive_account_cannot_login(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.login().status_code, 401)

    def test_bearer_access_authenticates_existing_documents_api(self):
        tokens = self.login().json()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens['access'])
        self.assertEqual(self.client.get(self.protected_url).status_code, 200)

    def test_protected_api_rejects_missing_token(self):
        self.assertEqual(self.client.get(self.protected_url).status_code, 401)

    def test_refresh_rotates_and_blacklists_previous_token(self):
        tokens = self.login().json()
        response = self.refresh(tokens['refresh'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'access', 'refresh'})
        self.assertNotEqual(response.json()['refresh'], tokens['refresh'])
        self.assertEqual(self.refresh(tokens['refresh']).status_code, 401)
        self.assertEqual(self.refresh(response.json()['refresh']).status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.json()['access'])
        self.assertEqual(self.client.get(self.protected_url).status_code, 200)

    def test_logout_revokes_refresh_but_existing_access_remains_valid(self):
        tokens = self.login().json()
        response = self.logout(tokens['refresh'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})
        self.assertTrue(BlacklistedToken.objects.exists())
        self.assertEqual(self.refresh(tokens['refresh']).status_code, 401)
        self.assertEqual(self.logout(tokens['refresh']).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens['access'])
        self.assertEqual(self.client.get(self.protected_url).status_code, 200)

    def test_expired_tampered_and_wrong_type_access_tokens(self):
        access = AccessToken.for_user(self.user)
        expired = AccessToken.for_user(self.user)
        expired.set_exp(from_time=timezone.now(), lifetime=timedelta(seconds=-1))
        refresh = RefreshToken.for_user(self.user)
        for token in (str(expired), str(access) + 'tampered', str(refresh), 'invalid'):
            with self.subTest(kind='invalid access'):
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                self.assertEqual(self.client.get(self.protected_url).status_code, 401)

    def test_refresh_and_logout_reject_invalid_expired_or_wrong_type_tokens(self):
        expired = RefreshToken.for_user(self.user)
        expired.set_exp(from_time=timezone.now(), lifetime=timedelta(seconds=-1))
        valid = str(RefreshToken.for_user(self.user))
        access = str(AccessToken.for_user(self.user))
        for token in ('invalid', valid + 'tampered', str(expired), access):
            with self.subTest(kind='invalid refresh'):
                self.assertEqual(self.refresh(token).status_code, 401)
                self.assertEqual(self.logout(token).status_code, 401)
        for url in (REFRESH, LOGOUT):
            self.assertEqual(self.client.post(url, {}, format='json').status_code, 400)

    def test_deactivated_account_tokens_are_rejected(self):
        tokens = self.login().json()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.refresh(tokens['refresh']).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens['access'])
        self.assertEqual(self.client.get(self.protected_url).status_code, 401)

    def test_deleted_account_refresh_returns_401_instead_of_500(self):
        tokens = self.login().json()
        self.user.delete()
        self.assertEqual(self.refresh(tokens['refresh']).status_code, 401)

    def test_public_endpoints_ignore_stale_access_header(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get('/api/health/').status_code, 200)
