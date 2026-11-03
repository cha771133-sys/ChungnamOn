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
