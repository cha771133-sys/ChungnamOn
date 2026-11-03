from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from .models import UserProfile
from .serializers import (
    ActiveUserTokenRefreshSerializer,
    EmailTokenObtainPairSerializer,
    MeSerializer,
    ProfileUpdateSerializer,
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


class MeView(generics.GenericAPIView):
    serializer_class = MeSerializer

    def get(self, request):
        user = get_user_model().objects.select_related('profile').get(pk=request.user.pk)
        return Response(self.get_serializer(user).data)

    def patch(self, request):
        with transaction.atomic():
            # Lock the user as well: a profile may not exist yet on the first PATCH.
            user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            profile = UserProfile.objects.filter(user=user).first()
            serializer = ProfileUpdateSerializer(profile, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            user.profile = serializer.save(user=user)
        return Response(self.get_serializer(user).data)
