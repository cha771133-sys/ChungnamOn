from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from sources.agency_fields import validate_lookup_text
from sources.services.agency_lookup import ApplicabilityStatus, LookupStatus


class AgencyLookupQuerySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, trim_whitespace=False, help_text='공식 기관명 또는 등록된 별칭')
    region = serializers.CharField(required=False, max_length=100, trim_whitespace=False, help_text='기관의 관할 지역, 거주지 자동 판정 아님')

    def validate(self, attrs):
        errors = {}
        for field, maximum in (('name', 255), ('region', 100)):
            if field in attrs:
                try:
                    attrs[field] = validate_lookup_text(attrs[field], maximum=maximum)
                except DjangoValidationError as error:
                    errors[field] = error.messages
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class AgencyResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    department = serializers.CharField(allow_blank=True)
    region = serializers.CharField()
    phone = serializers.CharField(allow_null=True, allow_blank=True)
    phoneType = serializers.CharField(source='phone_type', help_text='REPRESENTATIVE 대표·상담 / DEPARTMENT 부서 / UNKNOWN 미확인')
    sourceUrl = serializers.URLField(source='source_url', allow_null=True, allow_blank=True)
    checkedAt = serializers.DateTimeField(source='checked_at', allow_null=True)

    def to_representation(self, instance):
        result = super().to_representation(instance)
        for field in ('phone', 'sourceUrl'):
            result[field] = result[field] or None
        return result


class AgencyLookupResponseSerializer(serializers.Serializer):
    lookupStatus = serializers.ChoiceField(source='lookup_status', choices=LookupStatus.choices)
    applicabilityStatus = serializers.ChoiceField(source='applicability_status', choices=ApplicabilityStatus.choices)
    agency = AgencyResponseSerializer(allow_null=True)
    candidates = AgencyResponseSerializer(many=True, help_text='AMBIGUOUS일 때만 후보를 제공하며 하나를 임의 확정하지 않음')
    message = serializers.CharField()
