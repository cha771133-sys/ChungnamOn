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
