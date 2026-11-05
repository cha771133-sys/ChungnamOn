from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from core.schema import error_response

from .models import UserProfile
from .serializers import (
    ActiveUserTokenRefreshSerializer,
    EmailTokenObtainPairSerializer,
    MeSerializer,
    ProfileUpdateSerializer,
    RegistrationSerializer,
    TokenPairResponseSerializer,
)


@extend_schema_view(post=extend_schema(
    operation_id='auth_register', summary='이메일 회원가입', auth=[],
    responses={
        201: RegistrationSerializer,
        400: error_response('VALIDATION_ERROR: 이메일·비밀번호 검증 실패 또는 중복 이메일'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        415: error_response('UNSUPPORTED_MEDIA_TYPE: 지원하지 않는 Content-Type'),
    },
    tags=['인증'],
))
@method_decorator(sensitive_post_parameters(), name='dispatch')
class RegistrationView(generics.CreateAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = RegistrationSerializer


@extend_schema_view(post=extend_schema(
    operation_id='auth_login', summary='이메일 로그인', auth=[],
    responses={
        200: TokenPairResponseSerializer,
        400: error_response('VALIDATION_ERROR: 필수 입력값 누락 또는 형식 오류'),
        401: error_response('AUTHENTICATION_FAILED: 로그인 정보가 유효하지 않음'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        415: error_response('UNSUPPORTED_MEDIA_TYPE: 지원하지 않는 Content-Type'),
    },
    tags=['인증'],
))
@method_decorator(sensitive_post_parameters(), name='dispatch')
class LoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer


@extend_schema_view(post=extend_schema(
    operation_id='auth_token_refresh', summary='JWT 갱신', auth=[],
    description='Access Token은 15분, Refresh Token은 7일입니다. 갱신 시 이전 Refresh Token을 차단하고 두 토큰을 함께 반환합니다.',
    responses={
        200: TokenPairResponseSerializer,
        400: error_response('VALIDATION_ERROR: refresh 입력 누락 또는 형식 오류'),
        401: error_response('AUTHENTICATION_FAILED: 만료·무효·차단된 토큰 또는 사용할 수 없는 계정'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        415: error_response('UNSUPPORTED_MEDIA_TYPE: 지원하지 않는 Content-Type'),
    },
    tags=['인증'],
))
@method_decorator(sensitive_post_parameters(), name='dispatch')
class RefreshView(TokenRefreshView):
    serializer_class = ActiveUserTokenRefreshSerializer


@extend_schema_view(post=extend_schema(
    operation_id='auth_logout', summary='로그아웃', auth=[],
    description='제출한 Refresh Token만 차단합니다. 기존 Access Token은 만료까지 유효할 수 있습니다.',
    responses={
        200: OpenApiResponse(response={'type': 'object', 'additionalProperties': False}, description='빈 객체 {}'),
        400: error_response('VALIDATION_ERROR: refresh 입력 누락 또는 형식 오류'),
        401: error_response('AUTHENTICATION_FAILED: 만료·무효·이미 차단된 토큰'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        415: error_response('UNSUPPORTED_MEDIA_TYPE: 지원하지 않는 Content-Type'),
    },
    tags=['인증'],
))
@method_decorator(sensitive_post_parameters(), name='dispatch')
class LogoutView(TokenBlacklistView):
    pass


@extend_schema_view(
    get=extend_schema(
        operation_id='auth_me', summary='내 프로필 조회',
        responses={
            200: MeSerializer,
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        }, tags=['프로필'],
    ),
    patch=extend_schema(
        operation_id='auth_me_update', summary='내 프로필 수정',
        description='최초 설정에는 language·nationality·visaType 세 항목이 모두 필요합니다. 설정 완료 후 부분 수정할 수 있습니다.',
        request=ProfileUpdateSerializer,
        responses={
            200: MeSerializer,
            400: error_response('VALIDATION_ERROR: 필수 프로필 값 누락, 잘못된 코드 또는 변경 불가 필드'),
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
            415: error_response('UNSUPPORTED_MEDIA_TYPE: 지원하지 않는 Content-Type'),
        }, tags=['프로필'],
    ),
)
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
