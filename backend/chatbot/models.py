from django.core.exceptions import ValidationError
from django.db import models


class ChatMessage(models.Model):
    class Status(models.TextChoices):
        GROUNDED = 'GROUNDED', '공식 근거 기반'
        UNVERIFIED = 'UNVERIFIED', '공식 근거 미확인'
        MISMATCH = 'MISMATCH', '문서와 공식 근거 불일치'

    document = models.ForeignKey(
        'documents.Document', on_delete=models.CASCADE, related_name='chat_messages',
    )
    question = models.TextField()
    answer = models.TextField()
    language = models.CharField(max_length=10)
    grounded = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNVERIFIED)
    sources = models.JSONField(default=list, blank=True)
    suggested_questions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(
            fields=['document', '-created_at', '-id'], name='chat_document_created_idx',
        )]
        constraints = [
            models.CheckConstraint(condition=~models.Q(question=''), name='chat_question_not_empty'),
            models.CheckConstraint(condition=~models.Q(answer=''), name='chat_answer_not_empty'),
            models.CheckConstraint(
                condition=(models.Q(status='GROUNDED', grounded=True)
                           | models.Q(status__in=['UNVERIFIED', 'MISMATCH'], grounded=False)),
                name='chat_grounded_status_consistent',
            ),
        ]

    def clean(self):
        super().clean()
        if self.grounded != (self.status == self.Status.GROUNDED):
            raise ValidationError('근거 상태와 grounded가 일치해야 합니다.')
        if not isinstance(self.sources, list) or any(not isinstance(s, dict) for s in self.sources):
            raise ValidationError({'sources': '출처는 객체 배열이어야 합니다.'})
        if self.status in (self.Status.GROUNDED, self.Status.MISMATCH) and not self.sources:
            raise ValidationError({'sources': '확인된 근거가 필요합니다.'})
        questions = self.suggested_questions
        if (not isinstance(questions, list) or len(questions) > 5
                or any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in questions)
                or len(set(questions)) != len(questions)):
            raise ValidationError({'suggested_questions': '중복 없는 200자 이하 문자열을 최대 5개 허용합니다.'})

    def save(self, *args, **kwargs):
        for field in ('question', 'answer'):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError({field: '빈 문자열은 저장할 수 없습니다.'})
            setattr(self, field, value.strip())
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'ChatMessage {self.pk} (document {self.document_id})'
