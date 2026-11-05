from django.db import DatabaseError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.schema import error_response
from .models import NotificationPreference, PushDevice
from .serializers import (
    DeviceRegistrationSerializer, DeviceSerializer, PreferenceSerializer, PreferenceUpdateSerializer,
)
from .services.devices import register_device, set_preference

ERRORS = {
    400: error_response('VALIDATION_ERROR: 입력 필드 또는 값이 잘못되었습니다.'),
    401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer JWT 필요'),
    404: error_response('NOT_FOUND: 현재 사용자 소유의 기기가 없습니다.'),
    503: error_response('NOTIFICATION_STORAGE_UNAVAILABLE: 저장소 오류. 재요청 가능합니다.'),
}


class NotificationAPIView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, DatabaseError):
            return Response({
                'code': 'NOTIFICATION_STORAGE_UNAVAILABLE',
                'message': '알림 설정을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.', 'details': {},
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return super().handle_exception(exc)


@method_decorator(sensitive_post_parameters('registrationId'), name='dispatch')
class DeviceRegistrationView(NotificationAPIView):
    serializer_class = DeviceRegistrationSerializer

    @extend_schema(
        summary='푸시 기기 등록·갱신', tags=['알림'],
        description=(
            'FID 방식만 지원합니다. Firebase Web SDK register()로 푸시 등록한 FID를 사용합니다. '
            'TOKEN은 거부합니다. 같은 FID는 한 행으로 유지하며 계정 전환 시 현재 JWT 사용자로 이전합니다. '
            '이전 계정은 이후 이 기기에 접근할 수 없습니다. 등록은 수신 동의를 활성화하지 않습니다. '
            'FID 변경 시 replacesDeviceId로 기존 본인 등록을 함께 해제하세요. 응답에 FID 원문은 없습니다.'
        ),
        request=DeviceRegistrationSerializer, responses={200: DeviceSerializer, **ERRORS},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        device = register_device(
            user=request.user, registration_id=data['registrationId'],
            registration_type=data['registrationType'], platform=data['platform'],
            replaces_device_id=data.get('replacesDeviceId'),
        )
        return Response(DeviceSerializer(device).data)


class DeviceDeactivateView(NotificationAPIView):
    serializer_class = DeviceSerializer

    @extend_schema(
        summary='본인 푸시 기기 해제', tags=['알림'],
        description='비활성화 후에도 같은 사용자에게는 반복 요청 시 204입니다. 타 사용자 기기는 404입니다.',
        parameters=[OpenApiParameter('pk', int, OpenApiParameter.PATH, description='기기 등록 ID')],
        request=None, responses={204: None, **ERRORS},
    )
    def delete(self, request, pk):
        device = get_object_or_404(PushDevice.objects.filter(user=request.user), pk=pk)
        # 조회 직후 계정이 바뀌어도 새 소유자의 등록을 해제하지 않는다.
        PushDevice.objects.filter(pk=device.pk, user=request.user, is_active=True).update(
            is_active=False, updated_at=timezone.now(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        summary='본인 알림 설정 조회', tags=['알림'],
        description='미설정 시 enabled=false입니다. 활성 기기 수는 브라우저 권한이나 실제 수신을 보장하지 않습니다.',
        responses={200: PreferenceSerializer, **ERRORS},
    ),
    patch=extend_schema(
        summary='본인 마감 알림 수신 동의 변경', tags=['알림'],
        description=(
            'enabled는 JSON Boolean만 허용합니다. 재활성화 시각을 별도로 기록하여 향후 일정 연동에서 '
            '지난 알림을 제외할 수 있습니다. 현재 Schedule 미구현으로 자동 마감 알림은 제공되지 않습니다.'
        ),
        request=PreferenceUpdateSerializer, responses={200: PreferenceSerializer, **ERRORS},
    ),
)
class PreferenceView(NotificationAPIView):
    serializer_class = PreferenceSerializer

    def preference_data(self, user):
        enabled = NotificationPreference.objects.filter(user=user, enabled=True).exists()
        return {'enabled': enabled, 'registeredDeviceCount': PushDevice.objects.filter(user=user, is_active=True).count()}

    def get(self, request):
        return Response(self.get_serializer(self.preference_data(request.user)).data)

    def patch(self, request):
        serializer = PreferenceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_preference(request.user, serializer.validated_data['enabled'])
        return Response(self.get_serializer(self.preference_data(request.user)).data)
