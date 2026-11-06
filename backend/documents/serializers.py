from pathlib import Path

from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from .models import Document, InterpretationResult, Schedule

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}
MAX_FILE_SIZE = 20 * 1024 * 1024


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['file']
        extra_kwargs = {
            'file': {'help_text': 'jpg, jpeg, png, pdf 파일. 최대 20MB(20 × 1024 × 1024 바이트).'},
        }

    def validate_file(self, file):
        extension = Path(file.name).suffix.lower().lstrip('.')
        if extension not in ALLOWED_EXTENSIONS:
            raise serializers.ValidationError('jpg, jpeg, png, pdf 파일만 업로드할 수 있습니다.')
        if file.size > MAX_FILE_SIZE:
            raise serializers.ValidationError('파일은 최대 20MB까지 업로드할 수 있습니다.')
        return file

    def create(self, validated_data):
        file = validated_data['file']
        extension = Path(file.name).suffix.lower().lstrip('.')
        validated_data['file_name'] = file.name
        validated_data['file_type'] = (
            Document.FileType.PDF if extension == 'pdf' else Document.FileType.IMAGE
        )
        return super().create(validated_data)


class DocumentUploadResponseSerializer(serializers.Serializer):
    """실제 업로드 성공 응답에 대한 문서 전용 명세."""

    documentId = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Document.Status.choices)


class DocumentStatusSerializer(serializers.ModelSerializer):
    documentId = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = Document
        fields = ['documentId', 'status', 'step']
        read_only_fields = ['documentId', 'status', 'step']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.status == Document.Status.FAILED:
            data['failReason'] = instance.fail_reason
        return data


class DocumentStatusResponseSerializer(DocumentStatusSerializer):
    """실패 상태에서만 추가되는 failReason을 포함한 응답 명세."""

    failReason = serializers.CharField(required=False, help_text='FAILED 상태에서만 반환하는 실패 사유')

    class Meta(DocumentStatusSerializer.Meta):
        fields = [*DocumentStatusSerializer.Meta.fields, 'failReason']


class DocumentListSerializer(serializers.ModelSerializer):
    """내 문서함 목록 항목."""

    documentId = serializers.IntegerField(source='id', read_only=True)
    docType = serializers.CharField(source='doc_type', read_only=True)
    fileName = serializers.CharField(source='file_name', read_only=True)
    fileType = serializers.ChoiceField(source='file_type', choices=Document.FileType.choices, read_only=True)
    lowConfidence = serializers.BooleanField(source='low_confidence', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    expiresAt = serializers.DateTimeField(
        source='expires_at', read_only=True, help_text='원본 파일 자동 삭제 기준 시각 (업로드 후 30일)',
    )

    class Meta:
        model = Document
        fields = [
            'documentId', 'title', 'docType', 'fileName', 'fileType', 'status', 'step',
            'lowConfidence', 'createdAt', 'expiresAt',
        ]
        read_only_fields = fields


class DocumentDetailSerializer(DocumentListSerializer):
    """내 문서함 상세. 해석 결과 본문은 결과 조회 API에서 받는다."""

    failReason = serializers.CharField(
        source='fail_reason', read_only=True, help_text='FAILED 상태의 실패 사유. 그 외에는 빈 문자열',
    )
    maskedCount = serializers.IntegerField(source='masked_count', read_only=True)
    hasResult = serializers.SerializerMethodField(help_text='해석 결과가 저장되어 결과 조회 API를 쓸 수 있는지')

    class Meta(DocumentListSerializer.Meta):
        fields = [*DocumentListSerializer.Meta.fields, 'failReason', 'maskedCount', 'hasResult']
        read_only_fields = fields

    def get_hasResult(self, instance) -> bool:
        return hasattr(instance, 'result')


class DocumentNotReadySerializer(serializers.Serializer):
    """기존 처리 중·실패 409 응답의 명세."""

    detail = serializers.CharField()
    status = serializers.ChoiceField(choices=Document.Status.choices)
    step = serializers.ChoiceField(choices=Document.Step.choices)
    failReason = serializers.CharField(required=False, help_text='FAILED 상태에서만 반환하는 실패 사유')


class DocumentResultSerializer(serializers.ModelSerializer):
    """결과 조회 응답 형식. 형식이 바뀌면 이 serializer만 고친다."""

    documentId = serializers.IntegerField(source='document_id')
    docType = serializers.CharField(source='doc_type')
    originalText = serializers.CharField(source='original_text')
    translatedText = serializers.CharField(source='translated_text')
    requiredDocs = serializers.JSONField(source='required_docs')
    maskedCount = serializers.IntegerField(source='document.masked_count')
    lowConfidence = serializers.BooleanField(source='document.low_confidence')

    class Meta:
        model = InterpretationResult
        fields = [
            'documentId', 'docType', 'title', 'originalText', 'translatedText',
            'summary', 'terms', 'deadlines', 'requiredDocs', 'agency', 'verification',
            'maskedCount', 'lowConfidence',
        ]
        read_only_fields = fields


def get_result(document):
    """문서의 해석 결과. 없으면 None."""
    return document.result if hasattr(document, 'result') else None


def find_deadline_label(document, date):
    """해석 결과 deadlines에서 date와 같은 첫 항목의 label. 해당 날짜가 없으면 None."""
    result = get_result(document)
    if result is None:
        return None
    for deadline in result.deadlines or []:
        if isinstance(deadline, dict) and deadline.get('date') == date.isoformat():
            return deadline.get('label') or ''
    return None


class ScheduleSerializer(serializers.ModelSerializer):
    """일정 응답 형식. isOverdue·daysLeft는 한국 날짜(timezone.localdate) 기준이다."""

    scheduleId = serializers.IntegerField(source='id', read_only=True)
    documentId = serializers.IntegerField(source='document_id', read_only=True)
    documentTitle = serializers.CharField(source='document.title', read_only=True)
    doneAt = serializers.DateTimeField(source='done_at', read_only=True, allow_null=True)
    isOverdue = serializers.SerializerMethodField(help_text='미완료이고 마감일이 오늘보다 이전이면 true')
    daysLeft = serializers.SerializerMethodField(help_text='마감일 - 오늘. 0이면 오늘 마감, 음수면 지난 일수')
    agency = serializers.SerializerMethodField(help_text='문서 해석 결과의 담당 기관. 없으면 null')
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = Schedule
        fields = [
            'scheduleId', 'documentId', 'documentTitle', 'title', 'date', 'done', 'doneAt',
            'isOverdue', 'daysLeft', 'agency', 'createdAt', 'updatedAt',
        ]
        read_only_fields = fields

    def get_isOverdue(self, instance) -> bool:
        return not instance.done and instance.date < timezone.localdate()

    def get_daysLeft(self, instance) -> int:
        return (instance.date - timezone.localdate()).days

    def get_agency(self, instance) -> dict | None:
        result = get_result(instance.document)
        return (result.agency or None) if result is not None else None


class ScheduleCreateSerializer(serializers.Serializer):
    documentId = serializers.IntegerField(help_text='본인 소유의 DONE 문서 ID')
    date = serializers.DateField(help_text='해석 결과 deadlines의 date 중 하나 (YYYY-MM-DD)')
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=200,
        help_text='업무명. 비우면 "마감 항목 label - 문서 제목"으로 만든다',
    )

    def validate_documentId(self, value):
        user = self.context['request'].user
        document = Document.objects.select_related('result').filter(pk=value, owner=user).first()
        if document is None:
            # 타인 문서와 없는 문서는 다른 조회 API처럼 같은 404로 응답한다.
            raise NotFound()
        if document.status != Document.Status.DONE:
            raise serializers.ValidationError('해석이 완료된 문서만 일정으로 등록할 수 있습니다.')
        return document

    def validate(self, attrs):
        document = attrs['documentId']
        label = find_deadline_label(document, attrs['date'])
        if label is None:
            raise serializers.ValidationError({'date': ['문서의 마감일 중 하나를 선택하세요.']})
        title = attrs.get('title', '').strip()
        if not title:
            title = ' - '.join(part for part in (label, document.title) if part) or '등록한 행정업무'
        return {'document': document, 'date': attrs['date'], 'title': title[:200]}


class ScheduleUpdateSerializer(serializers.Serializer):
    done = serializers.BooleanField(help_text='완료 여부. true면 완료 시각을 기록하고 false면 비운다')

    def to_internal_value(self, data):
        unknown = sorted(set(data) - set(self.fields)) if isinstance(data, dict) else []
        if unknown:
            raise serializers.ValidationError({name: ['수정할 수 없는 필드입니다.'] for name in unknown})
        return super().to_internal_value(data)

    def update(self, instance, validated_data):
        done = validated_data['done']
        if not done:
            instance.done_at = None
        elif not instance.done:
            # 이미 완료된 일정을 다시 완료로 보내면 처음 완료한 시각을 유지한다.
            instance.done_at = timezone.now()
        instance.done = done
        instance.save(update_fields=['done', 'done_at', 'updated_at'])
        return instance


class ScheduleConflictSerializer(serializers.Serializer):
    """중복 일정 409 응답의 명세."""

    detail = serializers.CharField()
    scheduleId = serializers.IntegerField(help_text='이미 등록된 일정 ID')
