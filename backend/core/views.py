from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .schema import error_response


@extend_schema(
    operation_id='health',
    summary='서버 상태 확인',
    description='인증 없이 백엔드의 정상 응답 여부를 확인합니다. 슬래시 없는 경로도 지원합니다.',
    auth=[],
    responses={
        200: inline_serializer(name='HealthResponse', fields={'status': serializers.CharField()}),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
    },
    examples=[OpenApiExample('정상 응답', value={'status': 'ok'}, response_only=True)],
    tags=['상태'],
)
@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    return Response({'status': 'ok'})
