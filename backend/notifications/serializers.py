from rest_framework import serializers

from accounts.serializers import StrictFieldsMixin
from .models import PushDevice


class DeviceRegistrationSerializer(StrictFieldsMixin, serializers.Serializer):
    registrationId = serializers.RegexField(
        r'\A[A-Za-z0-9_-]{22}\Z', trim_whitespace=False, write_only=True,
        help_text='FCM Web SDK register()로 등록한 22자리 FID. getId()만으로는 푸시 등록되지 않습니다.',
    )
    registrationType = serializers.ChoiceField(choices=PushDevice.RegistrationType.choices)
    platform = serializers.ChoiceField(choices=PushDevice.Platform.choices)
    replacesDeviceId = serializers.IntegerField(
        min_value=1, required=False, write_only=True,
        help_text='FID 변경 시 기존 본인 기기 ID. 새 등록과 동시에 기존 등록을 비활성화합니다.',
    )


class DeviceSerializer(serializers.ModelSerializer):
    registrationType = serializers.CharField(source='registration_type', read_only=True)
    isActive = serializers.BooleanField(source='is_active', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = PushDevice
        fields = ['id', 'registrationType', 'platform', 'isActive', 'createdAt', 'updatedAt']
        read_only_fields = fields


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        """문자열과 숫자 변환 없이 JSON Boolean만 수신 동의로 인정한다."""
        if type(data) is not bool:
            self.fail('invalid', input=data)
        return data


class PreferenceUpdateSerializer(StrictFieldsMixin, serializers.Serializer):
    enabled = StrictBooleanField(required=True)


class PreferenceSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    registeredDeviceCount = serializers.IntegerField(min_value=0)
