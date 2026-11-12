from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

RETENTION_DAYS = 30


class Document(models.Model):
    class FileType(models.TextChoices):
        IMAGE = 'IMAGE', '이미지'
        PDF = 'PDF', 'PDF'

    class Status(models.TextChoices):
        PENDING = 'PENDING', '대기'
        PROCESSING = 'PROCESSING', '처리 중'
        DONE = 'DONE', '완료'
        FAILED = 'FAILED', '실패'

    class Step(models.TextChoices):
        QUEUED = 'QUEUED', '대기열'
        OCR = 'OCR', 'OCR'
        MASKING = 'MASKING', '마스킹'
        INTERPRETING = 'INTERPRETING', '해석'
        VERIFYING = 'VERIFYING', '검증'
        DONE = 'DONE', '완료'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    file = models.FileField(upload_to='documents/%Y/%m/', max_length=255)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=10, choices=FileType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    step = models.CharField(max_length=20, choices=Step.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # 원본 파일 자동 삭제 기준 시각 (업로드 후 30일)
    expires_at = models.DateTimeField(blank=True)

    # 2단계 이후 처리 결과용 필드
    fail_reason = models.TextField(blank=True)
    masked_count = models.PositiveIntegerField(default=0)
    low_confidence = models.BooleanField(default=False)
    doc_type = models.CharField(max_length=50, blank=True)
    title = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.id} {self.file_name}'

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = (self.created_at or timezone.now()) + timedelta(days=RETENTION_DAYS)
        super().save(*args, **kwargs)


class InterpretationResult(models.Model):
    """문서 해석 결과. JSON 필드 안의 형식은 services/pipeline.py의 docstring 참고."""

    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name='result')
    doc_type = models.CharField(max_length=50, blank=True)
    title = models.CharField(max_length=200, blank=True)
    # 마스킹된 원문
    original_text = models.TextField(blank=True)
    translated_text = models.TextField(blank=True)
    summary = models.JSONField(default=list)
    terms = models.JSONField(default=list)
    deadlines = models.JSONField(default=list)
    required_docs = models.JSONField(default=list)
    agency = models.JSONField(default=dict)
    # 납부 금액 예: "6,250원". 문서에 없으면 빈 문자열
    amount = models.CharField(max_length=100, blank=True)
    payment_methods = models.JSONField(default=list)
    verification = models.JSONField(default=dict)
    language = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.document_id} {self.title}'


class Schedule(models.Model):
    """해석 결과의 마감일 하나를 사용자가 등록한 일정. notifications 마감 알림의 대상이 된다."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='schedules',
    )
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='schedules')
    # 업무명
    title = models.CharField(max_length=200)
    # 마감일 (한국 날짜)
    date = models.DateField()
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'document', 'date', 'title'],
                name='unique_schedule_per_document_date_title',
            ),
        ]
        indexes = [models.Index(fields=['owner', 'date'], name='schedule_owner_date_idx')]

    def __str__(self):
        return f'{self.id} {self.date} {self.title}'


class ChecklistItem(models.Model):
    """해석(interpret)이 만든 체크리스트 항목. 문서를 다시 처리하면 새 항목으로 교체한다."""

    class State(models.TextChoices):
        CONFIRMED = 'CONFIRMED', '확정'
        CONDITIONAL = 'CONDITIONAL', '조건부'
        CHECK_NEEDED = 'CHECK_NEEDED', '확인 필요'

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='checklist_items')
    # interpret이 낸 순서 (0부터)
    order = models.PositiveIntegerField()
    text = models.CharField(max_length=300)
    detail = models.TextField(blank=True)
    # 준비할 서류 이름 목록
    docs = models.JSONField(default=list)
    method = models.TextField(blank=True)
    due = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.CHECK_NEEDED)
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    # 근거 출처 id 목록 (verification sources[].id)
    source_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['document', 'order'], name='unique_checklist_item_order'),
        ]

    def __str__(self):
        return f'{self.document_id}-{self.order} {self.text}'
