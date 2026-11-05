from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

FID = 'c' + 'a' * 21
OTHER_FID = 'd' + 'b' * 21


class NotificationAPITestCase(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='push@example.com', password='test-only-password')
        self.other = get_user_model().objects.create_user(email='other@example.com', password='test-only-password')
        self.authenticate(self.user)

    def authenticate(self, user):
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def register(self, fid=FID, **overrides):
        data = {'registrationId': fid, 'registrationType': 'FID', 'platform': 'WEB', **overrides}
        return self.client.post('/api/notifications/devices/', data, format='json')
