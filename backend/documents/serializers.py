from pathlib import Path

from rest_framework import serializers

from .models import Document, InterpretationResult

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
