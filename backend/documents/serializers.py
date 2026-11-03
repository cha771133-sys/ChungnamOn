from pathlib import Path

from rest_framework import serializers

from .models import Document

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
