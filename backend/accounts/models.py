from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator, validate_email
from django.db import models
from django.db.models.functions import Lower, Trim

from .managers import UserManager


class User(AbstractUser):
    username = None
    email = models.EmailField('이메일', unique=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower(Trim('email')), name='accounts_user_email_ci_unique'),
        ]

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email)
        validate_email(self.email)
        super().save(*args, **kwargs)


class UserProfile(models.Model):
    class Language(models.TextChoices):
        VIETNAMESE = 'vi', '베트남어'
        ENGLISH = 'en', '영어'
        CHINESE = 'zh', '중국어'
        UZBEK = 'uz', '우즈베크어'
        NEPALI = 'ne', '네팔어'
        THAI = 'th', '태국어'
        KOREAN = 'ko', '한국어'

    class VisaType(models.TextChoices):
        E9 = 'E-9', '비전문취업'
        H2 = 'H-2', '방문취업'
        F4 = 'F-4', '재외동포'
        F6 = 'F-6', '결혼이민'
        D2 = 'D-2', '유학'
        D4 = 'D-4', '일반연수'
        E7 = 'E-7', '특정활동'
        F2 = 'F-2', '거주'
        F5 = 'F-5', '영주'
        OTHER = 'ETC', '기타'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile',
    )
    language = models.CharField(max_length=2, choices=Language.choices, blank=True)
    nationality = models.CharField(
        max_length=2, blank=True,
        validators=[RegexValidator(r'\A[A-Z]{2}\Z', '국적은 영문 대문자 두 자리 코드로 입력하세요.')],
    )
    visa_type = models.CharField(max_length=3, choices=VisaType.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def onboarding_completed(self):
        return bool(self.language and self.nationality and self.visa_type)
