from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.views.decorators.debug import sensitive_variables
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer

from .models import NATIONALITY_HELP_TEXT, UserProfile

User = get_user_model()
DUPLICATE_EMAIL = '이미 사용 중인 이메일입니다.'


class StrictFieldsMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict):
            allowed = {name for name, field in self.fields.items() if not field.read_only}
            unknown = set(data) - allowed
            if unknown:
                raise serializers.ValidationError({
                    field: '변경할 수 없는 필드입니다.' for field in sorted(unknown)
                })
        return super().to_internal_value(data)


class RegistrationSerializer(StrictFieldsMixin, serializers.ModelSerializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(
        write_only=True, trim_whitespace=False, style={'input_type': 'password'},
    )

    class Meta:
        model = User
        fields = ('id', 'email', 'password')
        read_only_fields = ('id',)

    def validate_email(self, value):
        email = User.objects.normalize_email(value)
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(DUPLICATE_EMAIL)
        return email

    @sensitive_variables()
    def validate(self, attrs):
        try:
            validate_password(attrs['password'], user=User(email=attrs['email']))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages}) from exc
        return attrs

    @sensitive_variables()
    def create(self, validated_data):
        try:
            with transaction.atomic():
                return User.objects.create_user(**validated_data)
        except IntegrityError as exc:
            # The DB constraint also handles simultaneous registrations.
            if User.objects.filter(email__iexact=validated_data['email']).exists():
                raise serializers.ValidationError({'email': DUPLICATE_EMAIL}) from exc
            raise


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'] = serializers.EmailField(max_length=254, write_only=True)
        self.fields['password'].trim_whitespace = False

    def validate_email(self, value):
        return User.objects.normalize_email(value)


class ActiveUserTokenRefreshSerializer(TokenRefreshSerializer):
    @sensitive_variables()
    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except User.DoesNotExist as exc:
            # SimpleJWT 5.5.1 otherwise lets a deleted token owner raise a 500.
            raise AuthenticationFailed(
                self.error_messages['no_active_account'], code='no_active_account',
            ) from exc


class TokenPairResponseSerializer(serializers.Serializer):
    """로그인·갱신 성공 응답에 대한 명세."""

    access = serializers.CharField()
    refresh = serializers.CharField()


class MeSerializer(serializers.ModelSerializer):
    language = serializers.CharField(source='profile.language', read_only=True, default=None, allow_null=True)
    nationality = serializers.CharField(
        source='profile.nationality', read_only=True, default=None, allow_null=True,
        help_text=NATIONALITY_HELP_TEXT,
    )
    visaType = serializers.CharField(source='profile.visa_type', read_only=True, default=None, allow_null=True)
    onboardingCompleted = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'email', 'language', 'nationality', 'visaType', 'onboardingCompleted')
        read_only_fields = fields

    def get_onboardingCompleted(self, user) -> bool:
        try:
            return user.profile.onboarding_completed
        except UserProfile.DoesNotExist:
            return False


class ProfileUpdateSerializer(StrictFieldsMixin, serializers.ModelSerializer):
    visaType = serializers.ChoiceField(
        source='visa_type', choices=UserProfile.VisaType.choices, required=False,
    )

    class Meta:
        model = UserProfile
        fields = ('language', 'nationality', 'visaType')
        extra_kwargs = {
            'language': {'required': False, 'allow_blank': False},
            'nationality': {'required': False, 'allow_blank': False},
        }

    def validate(self, attrs):
        if self.instance is None or not self.instance.onboarding_completed:
            missing = {
                external: '초기 설정에는 세 항목이 모두 필요합니다.'
                for external, internal in (
                    ('language', 'language'), ('nationality', 'nationality'), ('visaType', 'visa_type'),
                )
                if internal not in attrs
            }
            if missing:
                raise serializers.ValidationError(missing)
        return attrs
