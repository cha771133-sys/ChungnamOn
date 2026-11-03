from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from .serializers import (
    ActiveUserTokenRefreshSerializer,
    EmailTokenObtainPairSerializer,
    RegistrationSerializer,
)


@method_decorator(sensitive_post_parameters(), name='dispatch')
class RegistrationView(generics.CreateAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = RegistrationSerializer


@method_decorator(sensitive_post_parameters(), name='dispatch')
class LoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer


@method_decorator(sensitive_post_parameters(), name='dispatch')
class RefreshView(TokenRefreshView):
    serializer_class = ActiveUserTokenRefreshSerializer


@method_decorator(sensitive_post_parameters(), name='dispatch')
class LogoutView(TokenBlacklistView):
    pass
