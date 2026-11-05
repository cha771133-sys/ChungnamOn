from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .fields import NormalizedURLField
from .agency_fields import AgencyTextField, validate_agency_source


class SourceDocument(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '확인 대기'
        ACTIVE = 'ACTIVE', '사용 중'
        INACTIVE = 'INACTIVE', '사용 중지'

    source_url = NormalizedURLField('출처 URL', max_length=2048, unique=True)
    title = models.CharField('제목', max_length=500)
    agency = models.CharField('발행기관명', max_length=255, db_index=True)
    region = models.CharField('적용 지역', max_length=100, db_index=True)
    category = models.CharField('자료 분류', max_length=100, db_index=True)
    published_at = models.DateField('발행일·기준일', null=True, blank=True)
    collected_at = models.DateTimeField('공식자료 확인일', null=True, blank=True)
    content_hash = models.CharField('본문 해시', max_length=128, blank=True)
    content_text = models.TextField('정제된 공식자료 본문', blank=True, default='')
    status = models.CharField(
        '자료 상태', max_length=10, choices=Status.choices, default=Status.PENDING,
    )

    class Meta:
        verbose_name = '공식자료'
        verbose_name_plural = '공식자료'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_url=''), name='sources_document_url_not_empty',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'ACTIVE', 'INACTIVE']),
                name='sources_document_status_valid',
            ),
        ]

    def __str__(self):
        return f'{self.title} ({self.agency})'


class SourceChunk(models.Model):
    source_document = models.ForeignKey(
        SourceDocument, on_delete=models.PROTECT, related_name='chunks', verbose_name='공식자료',
    )
    chunk_text = models.TextField('청크 본문')
    chunk_order = models.PositiveIntegerField('청크 순서')
    # 차원·벡터 검색 계약 확정 전의 저장 공간이다. 벡터 검색 기능은 제공하지 않는다.
    embedding = models.JSONField('임베딩', null=True, blank=True)

    class Meta:
        verbose_name = '공식자료 청크'
        verbose_name_plural = '공식자료 청크'
        ordering = ['source_document_id', 'chunk_order']
        constraints = [
            models.UniqueConstraint(
                fields=['source_document', 'chunk_order'],
                name='sources_chunk_document_order_uq',
            ),
        ]

    def __str__(self):
        return f'공식자료 {self.source_document_id} / 청크 {self.chunk_order}'


class CrawlLog(models.Model):
    class Result(models.TextChoices):
        RUNNING = 'RUNNING', '진행 중'
        SUCCESS = 'SUCCESS', '성공'
        FAILED = 'FAILED', '실패'

    source_url = models.URLField('수집 대상 URL', max_length=2048)
    started_at = models.DateTimeField('시작 시각', default=timezone.now)
    finished_at = models.DateTimeField('종료 시각', null=True, blank=True)
    result = models.CharField(
        '실행 결과', max_length=10, choices=Result.choices, default=Result.RUNNING,
    )
    error_message = models.TextField(
        '오류 메시지', blank=True,
        help_text='수집 서비스에서 비밀번호·API 키·토큰을 제거한 오류만 저장해야 합니다.',
    )

    class Meta:
        verbose_name = '수집 실행 이력'
        verbose_name_plural = '수집 실행 이력'
        ordering = ['-started_at', '-id']
        indexes = [
            models.Index(fields=['-started_at', '-id'], name='sources_log_started_idx'),
            models.Index(
                fields=['source_url', '-started_at', '-id'], name='sources_log_url_start_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(finished_at__isnull=True)
                | models.Q(finished_at__gte=models.F('started_at')),
                name='sources_log_finished_after_start',
            ),
            models.CheckConstraint(
                condition=~models.Q(source_url=''), name='sources_log_url_not_empty',
            ),
            models.CheckConstraint(
                condition=models.Q(result__in=['RUNNING', 'SUCCESS', 'FAILED']),
                name='sources_log_result_valid',
            ),
        ]

    def clean(self):
        super().clean()
        if self.finished_at and self.started_at and self.finished_at < self.started_at:
            raise ValidationError({'finished_at': '종료 시각은 시작 시각보다 빠를 수 없습니다.'})

    def __str__(self):
        return f'{self.source_url} ({self.get_result_display()})'


class Agency(models.Model):
    class PhoneType(models.TextChoices):
        REPRESENTATIVE = 'REPRESENTATIVE', '대표·상담 번호'
        DEPARTMENT = 'DEPARTMENT', '부서 번호'
        UNKNOWN = 'UNKNOWN', '미확인'

    name = AgencyTextField('공식 기관명', max_length=255, db_index=True)
    department = AgencyTextField('담당 부서', max_length=255, blank=True, default='')
    region = AgencyTextField('관할 지역', max_length=100, db_index=True)
    phone = models.CharField('공식 연락처', max_length=100, null=True, blank=True)
    phone_type = models.CharField(
        '연락처 구분', max_length=14, choices=PhoneType.choices, default=PhoneType.UNKNOWN,
    )
    source_url = NormalizedURLField('공식 출처 URL', max_length=2048, null=True, blank=True)
    checked_at = models.DateTimeField('기관 정보 확인일', null=True, blank=True)
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)
    updated_at = models.DateTimeField('수정 시각', auto_now=True)

    class Meta:
        verbose_name = '담당기관'
        verbose_name_plural = '담당기관'
        ordering = ['name', 'region', 'department', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'department', 'region'], name='sources_agency_identity_uq',
            ),
            models.CheckConstraint(condition=~models.Q(name=''), name='sources_agency_name_required'),
            models.CheckConstraint(condition=~models.Q(region=''), name='sources_agency_region_required'),
            models.CheckConstraint(
                condition=models.Q(phone_type__in=['REPRESENTATIVE', 'DEPARTMENT', 'UNKNOWN']),
                name='sources_agency_phone_type_valid',
            ),
        ]

    def clean(self):
        super().clean()
        if self.source_url:
            try:
                self.source_url = validate_agency_source(self.source_url)
            except (ValidationError, ValueError):
                raise ValidationError({'source_url': '허용된 공식 HTTPS 출처가 필요합니다.'}) from None
        if self.checked_at is not None and (
            not isinstance(self.checked_at, datetime) or not self.source_url
            or timezone.is_naive(self.checked_at) or self.checked_at > timezone.now()
        ):
            raise ValidationError({'checked_at': '공식 출처와 시간대가 있는 과거 확인 시각이 필요합니다.'})
        if self.phone and (not self.source_url or not self.checked_at):
            raise ValidationError({'phone': '연락처는 공식 출처와 실제 확인일을 함께 기록해야 합니다.'})
        if not self.phone and self.phone_type != self.PhoneType.UNKNOWN:
            raise ValidationError({'phone_type': '연락처가 없으면 미확인으로 기록해 주세요.'})

    def __str__(self):
        department = f' / {self.department}' if self.department else ''
        return f'{self.name}{department} ({self.region})'


class AgencyAlias(models.Model):
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='aliases', verbose_name='담당기관')
    name = AgencyTextField('공식 표기·합의된 별칭', max_length=255, db_index=True)

    class Meta:
        verbose_name = '담당기관 별칭'
        verbose_name_plural = '담당기관 별칭'
        ordering = ['name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['agency', 'name'], name='sources_agency_alias_uq'),
            models.CheckConstraint(condition=~models.Q(name=''), name='sources_agency_alias_required'),
        ]

    def __str__(self):
        return f'{self.name} → {self.agency}'
