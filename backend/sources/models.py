from datetime import datetime

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models
from django.utils import timezone

from .fields import NormalizedURLField
from .agency_fields import AgencyTextField, validate_agency_source


class SourceDocument(models.Model):
    class Provider(models.TextChoices):
        HTML = 'html', '공식 HTML'
        PUBLIC_DATA = 'public_data', '공공데이터포털'
        LAW = 'law', '법제처'

    class ReviewStatus(models.TextChoices):
        PENDING = 'PENDING_REVIEW', '검토 대기'
        APPROVED = 'APPROVED', '승인'
        REJECTED = 'REJECTED', '반려'

    class Status(models.TextChoices):
        PENDING = 'PENDING', '확인 대기'
        ACTIVE = 'ACTIVE', '사용 중'
        INACTIVE = 'INACTIVE', '사용 중지'

    source_url = NormalizedURLField('출처 URL', max_length=2048, unique=True, null=True, blank=True)
    provider = models.CharField('수집 제공자', max_length=12, choices=Provider.choices, default=Provider.HTML)
    external_id = models.CharField('외부 자료 ID', max_length=100, blank=True, default='')
    title = models.CharField('제목', max_length=500)
    agency = models.CharField('발행기관명', max_length=255, db_index=True)
    region = models.CharField('적용 지역', max_length=100, db_index=True)
    category = models.CharField('자료 분류', max_length=100, db_index=True)
    published_at = models.DateField('발행일·기준일', null=True, blank=True)
    collected_at = models.DateTimeField('공식자료 확인일', null=True, blank=True)
    content_hash = models.CharField('본문 해시', max_length=128, blank=True)
    content_text = models.TextField('정제된 공식자료 본문', blank=True, default='')
    effective_at = models.DateField('공식 시행일', null=True, blank=True)
    metadata = models.JSONField('제공자 원본 메타데이터', default=dict, blank=True)
    last_success_at = models.DateTimeField('마지막 수집 성공일', null=True, blank=True)
    observed_started_at = models.DateTimeField('최근 성공 작업 시작일', null=True, blank=True)
    observed_hash = models.CharField('최근 확인 본문 해시', max_length=64, blank=True, default='')
    updated_at = models.DateTimeField('저장 정보 수정일', auto_now=True)
    version = models.PositiveIntegerField('승인 정보 버전', default=0)
    review_status = models.CharField('관리자 검토', max_length=14, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='검토 관리자')
    reviewed_at = models.DateTimeField('관리자 검토일', null=True, blank=True)
    ai_reprocessing_required = models.BooleanField('AI 재처리 필요', default=False)
    status = models.CharField(
        '자료 상태', max_length=10, choices=Status.choices, default=Status.PENDING,
    )

    class Meta:
        verbose_name = '공식자료'
        verbose_name_plural = '공식자료'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_url='') & (models.Q(source_url__isnull=False) | (~models.Q(provider='html') & ~models.Q(external_id=''))),
                name='sources_document_url_not_empty',
            ),
            models.UniqueConstraint(fields=['provider', 'external_id'], condition=~models.Q(external_id=''), name='sources_provider_external_uq'),
            models.CheckConstraint(condition=models.Q(provider__in=['html', 'public_data', 'law']), name='sources_provider_valid'),
            models.CheckConstraint(condition=models.Q(provider='html', external_id='') | (models.Q(provider__in=['public_data', 'law']) & ~models.Q(external_id='')), name='sources_document_identity'),
            models.CheckConstraint(condition=models.Q(review_status__in=['PENDING_REVIEW', 'APPROVED', 'REJECTED']), name='sources_review_valid'),
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'ACTIVE', 'INACTIVE']),
                name='sources_document_status_valid',
            ),
        ]

    def __str__(self):
        return f'{self.title} ({self.agency})'

    def clean(self):
        """HTML 출처와 API 외부 ID의 필수 조건을 각각 검증한다."""
        super().clean()
        if self.provider == self.Provider.HTML and not self.source_url:
            raise ValidationError({'source_url': 'HTML 자료의 출처 URL은 필수입니다.'})
        if self.provider != self.Provider.HTML and not self.external_id:
            raise ValidationError({'external_id': 'API 자료의 외부 ID는 필수입니다.'})

    @property
    def ai_eligible(self):
        """공식 링크·승인·사용 상태가 갖춰지고 AI 재처리가 끝난 자료만 허용한다."""
        return bool(self.source_url) and self.status == self.Status.ACTIVE and self.review_status == self.ReviewStatus.APPROVED and not self.ai_reprocessing_required


class SourceChunk(models.Model):
    source_document = models.ForeignKey(
        SourceDocument, on_delete=models.PROTECT, related_name='chunks', verbose_name='공식자료',
    )
    chunk_text = models.TextField('청크 본문')
    chunk_order = models.PositiveIntegerField('청크 순서')
    # 차원·벡터 검색 계약 확정 전의 저장 공간이다. 벡터 검색 기능은 제공하지 않는다.
    embedding = models.JSONField('임베딩', null=True, blank=True)
    source_content_hash = models.CharField('청크 생성 당시 원문 해시', max_length=64, blank=True, default='')

    @property
    def is_current(self):
        """사용 가능한 문서의 승인 본문 해시와 일치하는 청크만 최신으로 본다."""
        document = self.source_document
        return bool(self.source_content_hash) and self.source_content_hash == document.content_hash and document.ai_eligible

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
    target = models.ForeignKey('SourceRefreshTarget', on_delete=models.PROTECT, null=True, blank=True, related_name='crawl_logs', verbose_name='등록 수집 대상')
    provider = models.CharField('수집 제공자', max_length=12, blank=True, default='')
    external_id = models.CharField('외부 자료 ID', max_length=100, blank=True, default='')
    error_code = models.CharField('오류 유형', max_length=40, blank=True, default='')
    outcome = models.CharField('처리 구분', max_length=20, blank=True, default='')
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


class SourceRefreshTarget(models.Model):
    provider = models.CharField('수집 제공자', max_length=12, choices=SourceDocument.Provider.choices)
    external_id = models.CharField('외부 자료 ID', max_length=100, blank=True, default='')
    source_url = NormalizedURLField('공식 상세 URL', max_length=2048, null=True, blank=True)
    document = models.OneToOneField(SourceDocument, on_delete=models.PROTECT, null=True, blank=True, related_name='refresh_target', verbose_name='기존 정상 자료')
    configuration = models.JSONField('등록 메타데이터', default=dict, blank=True)
    enabled = models.BooleanField('수집 활성', default=True)
    interval_hours = models.PositiveIntegerField('재확인 주기(시간, 비우면 유형 기본값)', null=True, blank=True)
    last_attempt_at = models.DateTimeField('마지막 시도일', null=True, blank=True)
    last_success_at = models.DateTimeField('마지막 성공 확인일', null=True, blank=True)
    failures = models.PositiveIntegerField('연속 실패 횟수', default=0)
    retry_after = models.DateTimeField('실패 재시도 예정일', null=True, blank=True)
    lease_token = models.UUIDField('실행 소유 토큰', null=True, blank=True)
    lease_expires_at = models.DateTimeField('실행 잠금 만료일', null=True, blank=True)

    class Meta:
        verbose_name = '공식자료 수집 대상'
        verbose_name_plural = '공식자료 수집 대상'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['provider', 'external_id'], condition=~models.Q(external_id=''), name='sources_target_external_uq'),
            models.UniqueConstraint(fields=['source_url'], condition=models.Q(provider='html'), name='sources_target_html_uq'),
            models.CheckConstraint(condition=(models.Q(provider='html', source_url__isnull=False) & ~models.Q(source_url='')) | (models.Q(provider__in=['law', 'public_data']) & ~models.Q(external_id='')), name='sources_target_identity'),
            models.CheckConstraint(condition=models.Q(interval_hours__isnull=True) | models.Q(interval_hours__gt=0), name='sources_target_interval_valid'),
        ]

    def clean(self):
        """제공자별 식별·법제처 수집 관할·공개 링크를 검증하여 안전한 대상만 등록한다."""
        from .services.normalization import official_url
        super().clean()
        if not isinstance(self.configuration, dict):
            raise ValidationError({'configuration': '등록 메타데이터는 JSON 객체여야 합니다.'})
        if self.provider == 'law' and self.configuration.get('collection_region') not in ('충청남도', '천안시', '아산시'):
            raise ValidationError({'configuration': '법제처 대상의 collection_region에 충청남도·천안시·아산시 중 하나를 지정하세요.'})
        if self.provider == 'html' and self.external_id:
            raise ValidationError({'external_id': 'HTML 자료는 외부 API ID를 사용하지 않습니다.'})
        if self.provider == 'html' and not self.source_url:
            raise ValidationError({'source_url': 'HTML 대상에는 공식 URL이 필요합니다.'})
        if self.provider != 'html' and not self.external_id:
            raise ValidationError({'external_id': 'API 대상에는 외부 ID가 필요합니다.'})
        if self.source_url:
            try:
                self.source_url = official_url(self.source_url, self.provider)
            except Exception:
                raise ValidationError({'source_url': '인증정보 없는 허용된 공식 HTTPS URL이 필요합니다.'}) from None

    def __str__(self):
        """API는 외부 ID, HTML은 공식 URL로 수집 대상을 구분한다."""
        return f'{self.provider}: {self.external_id or self.source_url}'


class SourceRevision(models.Model):
    document = models.ForeignKey(SourceDocument, on_delete=models.PROTECT, related_name='revisions', verbose_name='공식자료')
    previous_hash = models.CharField('이전 해시', max_length=128, blank=True)
    base_version = models.PositiveIntegerField('기준 승인 정보 버전')
    previous_content_text = models.TextField('이전 승인 본문', blank=True)
    previous_metadata = models.JSONField('이전 승인 정보', default=dict)
    content_hash = models.CharField('새 본문 해시', max_length=64)
    content_text = models.TextField('새 본문')
    metadata = models.JSONField('새 자료 메타데이터', default=dict)
    provider = models.CharField('수집 제공자', max_length=12, choices=SourceDocument.Provider.choices)
    collected_at = models.DateTimeField('실제 본문 확인일')
    created_at = models.DateTimeField('변경 후보 생성일', auto_now_add=True)
    review_status = models.CharField('검토 상태', max_length=14, choices=SourceDocument.ReviewStatus.choices, default=SourceDocument.ReviewStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='검토 관리자')
    reviewed_at = models.DateTimeField('검토일', null=True, blank=True)

    class Meta:
        verbose_name = '공식자료 변경 후보'
        verbose_name_plural = '공식자료 변경 후보'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['document', 'content_hash', 'base_version'], condition=models.Q(review_status='PENDING_REVIEW'), name='sources_revision_pending_uq'),
            models.CheckConstraint(condition=models.Q(review_status__in=['PENDING_REVIEW', 'APPROVED', 'REJECTED']), name='sources_revision_review_valid'),
        ]

    def __str__(self):
        return f'{self.document_id}: {self.content_hash[:12]} ({self.get_review_status_display()})'


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
