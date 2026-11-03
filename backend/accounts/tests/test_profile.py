from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import UserProfile

User = get_user_model()
ME = '/api/auth/me/'
PROFILE = {'language': 'vi', 'nationality': 'VN', 'visaType': 'E-9'}


class ProfileTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('person@example.com', 'Bamboo!River8042')
        cls.other = User.objects.create_user('other@example.com', 'Bamboo!River8042')

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.user)))

    def patch_profile(self, data, url=ME):
        return self.client.patch(url, data, format='json')

    def complete_profile(self):
        response = self.patch_profile(PROFILE)
        self.assertEqual(response.status_code, 200)
        return response

    def test_missing_profile_has_explicit_unset_state_without_creating_row(self):
        response = self.client.get(ME)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'id': self.user.id, 'email': self.user.email,
            'language': None, 'nationality': None, 'visaType': None,
            'onboardingCompleted': False,
        })
        self.assertFalse(UserProfile.objects.exists())

    def test_first_setup_and_subsequent_read(self):
        response = self.complete_profile()
        expected = {
            'id': self.user.id, 'email': self.user.email, **PROFILE, 'onboardingCompleted': True,
        }
        self.assertEqual(response.json(), expected)
        self.assertEqual(self.client.get(ME).json(), expected)
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.onboarding_completed)
        self.assertIsNotNone(profile.created_at)
        self.assertIsNotNone(profile.updated_at)

    def test_each_initial_field_is_required_and_no_partial_row_is_saved(self):
        for missing in PROFILE:
            with self.subTest(missing=missing):
                response = self.patch_profile({k: v for k, v in PROFILE.items() if k != missing})
                self.assertEqual(response.status_code, 400)
                self.assertIn(missing, response.json()['details'])
                self.assertFalse(UserProfile.objects.exists())
        response = self.patch_profile({})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()['details']), set(PROFILE))

    def test_existing_incomplete_profile_still_requires_all_fields(self):
        profile = UserProfile.objects.create(user=self.user, language='vi')
        self.assertFalse(self.client.get(ME).json()['onboardingCompleted'])
        response = self.patch_profile({'nationality': 'VN', 'visaType': 'E-9'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('language', response.json()['details'])
        profile.refresh_from_db()
        self.assertEqual(profile.nationality, '')
        self.assertTrue(self.complete_profile().json()['onboardingCompleted'])

    def test_completed_profile_allows_partial_updates(self):
        self.complete_profile()
        created_at = UserProfile.objects.get(user=self.user).created_at
        for data in ({'language': 'en'}, {'nationality': 'NP'}, {'visaType': 'D-2'}):
            with self.subTest(data=data):
                response = self.patch_profile(data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['onboardingCompleted'])
                for key, value in data.items():
                    self.assertEqual(response.json()[key], value)
        self.assertEqual(UserProfile.objects.count(), 1)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.created_at, created_at)
        self.assertGreater(profile.updated_at, created_at)
        self.assertEqual(self.client.get(ME).json(), {
            'id': self.user.id, 'email': self.user.email,
            'language': 'en', 'nationality': 'NP', 'visaType': 'D-2', 'onboardingCompleted': True,
        })

    def test_invalid_codes_are_rejected_before_setup(self):
        for field, value in (
            ('language', 'xx'), ('nationality', 'Vietnam'), ('nationality', 'vn'),
            ('nationality', '1!'), ('visaType', 'invalid'),
        ):
            with self.subTest(field=field, value=value):
                response = self.patch_profile({**PROFILE, field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()['details'])
                self.assertFalse(UserProfile.objects.exists())

    def test_blank_or_null_fields_cannot_complete_or_clear_profile(self):
        for complete in (False, True):
            if complete:
                self.complete_profile()
            for field in PROFILE:
                for value in ('', '  ', None):
                    with self.subTest(complete=complete, field=field, value=value):
                        response = self.patch_profile({**PROFILE, field: value})
                        self.assertEqual(response.status_code, 400)
                        self.assertIn(field, response.json()['details'])
            self.assertEqual(self.client.get(ME).json()['onboardingCompleted'], complete)

    def test_id_email_privileges_password_and_unknown_fields_are_rejected(self):
        for field, value in (
            ('id', self.other.id), ('user', self.other.id), ('user_id', self.other.id),
            ('email', self.other.email), ('is_staff', True), ('is_superuser', True),
            ('is_active', False), ('password', 'Changed!Pass9521'),
            ('onboardingCompleted', True), ('visa_type', 'D-2'), ('unknown', 'value'),
        ):
            with self.subTest(field=field):
                response = self.patch_profile({**PROFILE, field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()['details'])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.email, 'person@example.com')
        self.assertTrue(self.user.check_password('Bamboo!River8042'))
        self.assertFalse(UserProfile.objects.exists())

    def test_query_parameters_cannot_target_another_user(self):
        other_profile = UserProfile.objects.create(
            user=self.other, language='ko', nationality='KR', visa_type='ETC',
        )
        url = f'{ME}?id={self.other.id}&user_id={self.other.id}'
        self.assertEqual(self.client.get(url).json()['id'], self.user.id)
        response = self.patch_profile(PROFILE, url=url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], self.user.id)
        other_profile.refresh_from_db()
        self.assertEqual(other_profile.language, 'ko')
        self.assertEqual(other_profile.nationality, 'KR')
        self.assertEqual(other_profile.visa_type, 'ETC')
        self.assertEqual(self.client.get(f'/api/auth/me/{self.other.id}/').status_code, 404)

    def test_profile_requires_authentication(self):
        self.client.credentials()
        self.assertEqual(self.client.get(ME).status_code, 401)
        self.assertEqual(self.patch_profile(PROFILE).status_code, 401)
        self.assertFalse(UserProfile.objects.exists())

    def test_completed_profile_rejects_readonly_fields_without_partial_write(self):
        self.complete_profile()
        response = self.patch_profile({'language': 'en', 'is_staff': True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserProfile.objects.get(user=self.user).language, 'vi')

    def test_profile_rejects_non_object_payload(self):
        for data in ([], 'invalid'):
            with self.subTest(data=data):
                self.assertEqual(self.patch_profile(data).status_code, 400)

    def test_unsupported_methods_are_rejected(self):
        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method(ME, {}, format='json').status_code, 405)
