from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from accounts.models import UserProfile

from .policy import validate_properties


class KPIEvent(models.Model):
    class Type(models.TextChoices):
        ANALYSIS_STARTED = 'DOCUMENT_ANALYSIS_STARTED', '문서 분석 시작'
        INTERPRETATION_COMPLETED = 'DOCUMENT_INTERPRETATION_COMPLETED', '서버 해석 처리 완료'
        INTERPRETATION_FAILED = 'DOCUMENT_INTERPRETATION_FAILED', '문서 해석 실패'
        RESULT_PROVIDED = 'DOCUMENT_RESULT_PROVIDED', '결과 API 응답 제공'
        CHAT_ANSWER_CREATED = 'CHAT_ANSWER_CREATED', '챗봇 답변 저장'

    class DataKind(models.TextChoices):
        REAL = 'REAL', '실제 업무'
        DUMMY = 'DUMMY', '더미 처리'
        TEST = 'TEST', '테스트'
        UNKNOWN = 'UNKNOWN', '출처 미확인'

    class Entity(models.TextChoices):
        DOCUMENT = 'DOCUMENT', '문서'
        CHAT_MESSAGE = 'CHAT_MESSAGE', '챗봇 답변'

    event_type = models.CharField('이벤트 종류', max_length=40, choices=Type.choices)
    occurred_at = models.DateTimeField('발생 시각', default=timezone.now)
    document = models.ForeignKey(
        'documents.Document', on_delete=models.SET_NULL, null=True, related_name='kpi_events',
        verbose_name='관련 문서',
    )
    language = models.CharField('당시 안내 언어', max_length=2, choices=UserProfile.Language.choices, blank=True)
    entity_type = models.CharField('업무 종류', max_length=12, choices=Entity.choices)
    entity_id = models.PositiveBigIntegerField('업무 ID')
    idempotency_key = models.CharField(
        '중복 방지 해시', max_length=64, unique=True,
        validators=[RegexValidator(r'\A[0-9a-f]{64}\Z', 'SHA-256 해시여야 합니다.')],
    )
    data_kind = models.CharField('데이터 구분', max_length=7, choices=DataKind.choices, default=DataKind.UNKNOWN)
    properties = models.JSONField('허용된 상태 정보', default=dict, blank=True)
    created_at = models.DateTimeField('저장 시각', auto_now_add=True)

    class Meta:
        verbose_name = 'KPI 이벤트'
        verbose_name_plural = 'KPI 이벤트'
        ordering = ['-occurred_at', '-id']
        indexes = [
            models.Index(fields=['event_type', 'data_kind', 'occurred_at'], name='kpi_type_kind_time_idx'),
            models.Index(fields=['-occurred_at', '-id'], name='kpi_time_id_idx'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(event_type__in=[
                'DOCUMENT_ANALYSIS_STARTED', 'DOCUMENT_INTERPRETATION_COMPLETED',
                'DOCUMENT_INTERPRETATION_FAILED', 'DOCUMENT_RESULT_PROVIDED', 'CHAT_ANSWER_CREATED',
            ]), name='kpi_event_type_valid'),
            models.CheckConstraint(condition=models.Q(data_kind__in=['REAL', 'DUMMY', 'TEST', 'UNKNOWN']), name='kpi_data_kind_valid'),
            models.CheckConstraint(condition=models.Q(entity_type__in=['DOCUMENT', 'CHAT_MESSAGE']), name='kpi_entity_type_valid'),
            models.CheckConstraint(condition=models.Q(entity_id__gt=0), name='kpi_entity_id_positive'),
        ]

    def clean(self):
        super().clean()
        validate_properties(self.event_type, self.properties)
        if timezone.is_naive(self.occurred_at):
            raise ValidationError('발생 시각에는 시간대가 필요합니다.')
        expected = self.Entity.CHAT_MESSAGE if self.event_type == self.Type.CHAT_ANSWER_CREATED else self.Entity.DOCUMENT
        if self.entity_type != expected:
            raise ValidationError('이벤트와 업무 종류가 일치해야 합니다.')
        if self.document_id and expected == self.Entity.DOCUMENT and self.entity_id != self.document_id:
            raise ValidationError('문서와 업무 ID가 일치해야 합니다.')

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False, validate_constraints=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_event_type_display()} #{self.pk}'
