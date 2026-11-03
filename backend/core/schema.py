from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers


class APIErrorSerializer(serializers.Serializer):
    code = serializers.CharField(help_text='프론트엔드 분기용 오류 식별자')
    message = serializers.CharField(help_text='사용자에게 표시할 오류 설명')
    details = serializers.DictField(
        child=serializers.JSONField(), help_text='중첩 필드 검증 오류. 추가 정보가 없으면 빈 객체',
    )


def error_response(description):
    return OpenApiResponse(response=APIErrorSerializer, description=description)
