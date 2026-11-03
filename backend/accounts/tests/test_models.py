from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.admin import AccountCreationForm
from accounts.models import UserProfile
from documents.models import Document

User = get_user_model()
PASSWORD = 'Bamboo!River8042'


class UserTests(TestCase):
    def test_create_user_normalizes_email_and_hashes_password(self):
        user = User.objects.create_user('  Person@Example.COM  ', PASSWORD)
        user.refresh_from_db()
        self.assertEqual(user.email, 'person@example.com')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertNotIn('username', [field.name for field in User._meta.fields])
        self.assertEqual(User.USERNAME_FIELD, 'email')
        self.assertEqual(User.REQUIRED_FIELDS, [])

    def test_missing_email_is_rejected(self):
        for email in ('', '   ', None):
            with self.subTest(email=email), self.assertRaises(ValueError):
                User.objects.create_user(email, PASSWORD)

    def test_duplicate_email_is_rejected_by_database(self):
        User.objects.create_user('person@example.com', PASSWORD)
        for email in ('person@example.com', 'PERSON@EXAMPLE.COM', ' person@example.com '):
            with self.subTest(email=email), self.assertRaises(IntegrityError), transaction.atomic():
                # bulk_create bypasses save/manager normalization: exercise the DB constraint.
                User.objects.bulk_create([User(email=email, password='!')])

    def test_save_normalizes_email(self):
        user = User.objects.create_user('person@example.com', PASSWORD)
        user.email = '  UPDATED@Example.com '
        user.save(update_fields=['email'])
        user.refresh_from_db()
        self.assertEqual(user.email, 'updated@example.com')

    def test_create_superuser_and_email_authentication(self):
        user = User.objects.create_superuser('ADMIN@Example.com', PASSWORD)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertEqual(authenticate(email=' ADMIN@EXAMPLE.COM ', password=PASSWORD), user)

    def test_superuser_requires_permission_flags(self):
        for field in ('is_staff', 'is_superuser', 'is_active'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                User.objects.create_superuser('admin@example.com', PASSWORD, **{field: False})

    def test_admin_login_and_user_pages(self):
        User.objects.create_superuser('admin@example.com', PASSWORD)
        response = self.client.post(reverse('admin:login'), {
            'username': ' ADMIN@EXAMPLE.COM ', 'password': PASSWORD, 'next': reverse('admin:index'),
        })
        self.assertEqual(response.status_code, 302)
        for url in ('admin:accounts_user_add', 'admin:accounts_user_changelist'):
            self.assertEqual(self.client.get(reverse(url)).status_code, 200)

    def test_admin_creation_form_hashes_password_and_rejects_duplicate(self):
        form = AccountCreationForm(data={
            'email': '  NEW@Example.COM ', 'password1': PASSWORD, 'password2': PASSWORD,
        })
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, 'new@example.com')
        self.assertTrue(user.check_password(PASSWORD))
        duplicate = AccountCreationForm(data={
            'email': 'NEW@EXAMPLE.COM', 'password1': PASSWORD, 'password2': PASSWORD,
        })
        self.assertFalse(duplicate.is_valid())
        self.assertIn('email', duplicate.errors)

    def test_document_owner_and_profile_use_custom_user(self):
        user = User.objects.create_user('person@example.com', PASSWORD)
        document = Document.objects.create(
            owner=user, file='documents/notice.pdf', file_name='notice.pdf', file_type='PDF',
        )
        profile = UserProfile.objects.create(user=user)
        self.assertFalse(profile.onboarding_completed)
        self.assertEqual(document.owner, user)
        self.assertEqual(user.documents.get(), document)
        self.assertEqual(user.profile, profile)
