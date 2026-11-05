from rest_framework import serializers

from .models import ChatMessage


class QuestionSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=2000, trim_whitespace=True)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - {'message'}
            if unknown:
                raise serializers.ValidationError({key: ['허용되지 않는 필드입니다.'] for key in unknown})
            if 'message' in data and not isinstance(data['message'], str):
                raise serializers.ValidationError({'message': ['문자열을 입력하세요.']})
        return super().to_internal_value(data)


class ChatSourceSerializer(serializers.Serializer):
    sourceDocumentId = serializers.IntegerField(min_value=1)
    title = serializers.CharField()
    url = serializers.URLField()
    checkedAt = serializers.DateTimeField(help_text='답변 생성에 사용한 실제 수집·확인 시각 스냅샷')
    contentHash = serializers.CharField(help_text='답변 당시 공식자료 본문 해시', allow_blank=True)


class ChatMessageSerializer(serializers.ModelSerializer):
    chatId = serializers.IntegerField(source='id', read_only=True)
    documentId = serializers.IntegerField(source='document_id', read_only=True)
    sources = ChatSourceSerializer(many=True, read_only=True)
    suggestedQuestions = serializers.ListField(
        child=serializers.CharField(), source='suggested_questions', read_only=True,
    )
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = ChatMessage
        fields = [
            'chatId', 'documentId', 'question', 'answer', 'language', 'grounded',
            'status', 'sources', 'suggestedQuestions', 'createdAt',
        ]
        read_only_fields = fields
        extra_kwargs = {
            'grounded': {'help_text': '서버에서 확인한 공식 근거와 명시적인 AI 판단이 모두 있는 경우만 true'},
            'status': {'help_text': 'GROUNDED: 공식 근거 기반 / UNVERIFIED: 미확인 / MISMATCH: 확인된 충돌'},
        }
