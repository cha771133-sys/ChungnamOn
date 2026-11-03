from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from core.permissions import IsOwner
from .helpers import create_user


class MissingOwner:
    @property
    def owner(self):
        raise ObjectDoesNotExist


class IsOwnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user('owner')
        cls.other = create_user('other')

    def setUp(self):
        self.permission = IsOwner()
        self.object = SimpleNamespace(owner=self.owner)

    def request(self, user, method='GET'):
        return SimpleNamespace(user=user, method=method)

    def test_anonymous_user_is_denied_at_both_levels(self):
        request = self.request(AnonymousUser())
        self.assertFalse(self.permission.has_permission(request, None))
        self.assertFalse(self.permission.has_object_permission(request, None, self.object))

    def test_owner_can_read_update_and_delete(self):
        for method in ('GET', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method):
                request = self.request(self.owner, method)
                self.assertTrue(self.permission.has_permission(request, None))
                self.assertTrue(self.permission.has_object_permission(request, None, self.object))

    def test_non_owner_cannot_read_update_or_delete(self):
        for method in ('GET', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method):
                self.assertFalse(self.permission.has_object_permission(
                    self.request(self.other, method), None, self.object,
                ))

    def test_missing_null_unresolved_or_non_user_owner_is_denied(self):
        for obj in (object(), SimpleNamespace(owner=None), MissingOwner(),
                    SimpleNamespace(owner=self.owner.pk)):
            with self.subTest(obj=type(obj).__name__):
                self.assertFalse(self.permission.has_object_permission(
                    self.request(self.owner), None, obj,
                ))

    def test_unsaved_user_cannot_be_an_owner(self):
        user = type(self.owner)()
        self.assertFalse(self.permission.has_object_permission(
            self.request(user), None, SimpleNamespace(owner=user),
        ))

    def test_drf_object_check_raises_permission_denied(self):
        view = APIView()
        view.permission_classes = [IsOwner]
        request = APIRequestFactory().get('/')
        force_authenticate(request, self.other)
        request = view.initialize_request(request)
        view.check_permissions(request)
        with self.assertRaises(PermissionDenied):
            view.check_object_permissions(request, self.object)
