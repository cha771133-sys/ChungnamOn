from pathlib import Path

from rest_framework import serializers

from .models import Document, InterpretationResult

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}
MAX_FILE_SIZE = 20 * 1024 * 1024


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['file']

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


class DocumentStatusSerializer(serializers.ModelSerializer):
    documentId = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = Document
        fields = ['documentId', 'status', 'step']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.status == Document.Status.FAILED:
            data['failReason'] = instance.fail_reason
        return data


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
