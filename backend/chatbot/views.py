from django.db import DatabaseError
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, serializers, status
from rest_framework.response import Response

from core.pagination import StandardPagination
from core.permissions import IsOwner
from core.schema import error_response

from .serializers import ChatMessageSerializer, QuestionSerializer
from .services.chat_service import (
    ChatServiceUnavailable, DocumentNotReady, message_history, owned_document, submit_question,
)


class ChatHistorySerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ChatMessageSerializer(many=True)


MESSAGE_EXAMPLE = {
    'chatId': 1, 'documentId': 123, 'question': '필요한 서류를 확인하고 싶습니다.',
    'answer': '현재 공식 근거를 확인할 수 없습니다. 정확한 내용은 담당기관에 확인해 주세요.',
    'language': 'ko', 'grounded': False, 'status': 'UNVERIFIED',
    'sources': [], 'suggestedQuestions': [], 'createdAt': '2026-11-05T09:00:00+09:00',
}
AUTH_EXAMPLE = OpenApiExample('인증 필요', value={
    'code': 'NOT_AUTHENTICATED', 'message': '인증이 필요합니다.', 'details': {},
}, response_only=True, status_codes=['401'])
COMMON_ERRORS = {
    401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer JWT 누락 또는 무효'),
    404: error_response('NOT_FOUND: 현재 사용자 소유 문서가 없거나 페이지가 없음'),
    503: error_response('CHAT_SERVICE_UNAVAILABLE: AI 호출/응답 검증/DB 실패. 저장하지 않음'),
}
PARAMETER = OpenApiParameter('pk', int, OpenApiParameter.PATH, description='문서 ID')


@extend_schema_view(
    get=extend_schema(
        operation_id='document_chat_history', summary='문서별 대화 이력 조회', tags=['챗봇'],
        description=(
            'JWT 인증과 문서 소유권을 검사합니다. 문서 상태와 무관하게 기존 이력을 조회합니다. '
            '최신순(createdAt 내림차순, ID 내림차순), 기본 20개·최대 100개입니다. '
            'sources의 확인일과 본문 해시는 답변 생성 당시 스냅샷입니다.'
        ),
        parameters=[PARAMETER, OpenApiParameter('page', int, description='페이지 번호'),
                    OpenApiParameter('page_size', int, description='기본 20, 최대 100')],
        responses={200: ChatHistorySerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample('대화 이력', value={
                'count': 1, 'next': None, 'previous': None, 'results': [MESSAGE_EXAMPLE],
            }, response_only=True, status_codes=['200']),
            OpenApiExample('빈 대화 이력', value={
                'count': 0, 'next': None, 'previous': None, 'results': [],
            }, response_only=True, status_codes=['200']), AUTH_EXAMPLE,
        ],
    ),
    post=extend_schema(
        operation_id='document_chat_create', summary='문서에 질문 제출', tags=['챗봇'],
        description=(
            'JWT 인증 및 소유권 검사 후 DONE이고 low_confidence=false인 문서만 허용합니다. '
            '질문은 공백 제거 후 1~2000자이며 추가 필드는 거부합니다. 동일 질문도 별도로 저장합니다. '
            '기본 AI 모드는 로컬 더미이며 항상 UNVERIFIED/grounded=false입니다. '
            '언어는 프로필 → 해석 결과 → 프로젝트 기본 언어 순입니다. '
            'GROUNDED는 명시적 AI 판단과 서버에서 확인된 공식 근거가 모두 필요합니다. '
            'MISMATCH는 검증 결과와 AI가 모두 충돌을 명시하고 근거를 제공해야 합니다. '
            '근거가 없으면 UNVERIFIED이며 담당기관에 확인해야 합니다.'
        ),
        parameters=[PARAMETER], request=QuestionSerializer,
        responses={201: ChatMessageSerializer, 400: error_response('VALIDATION_ERROR: 질문 검증 오류'),
                   409: error_response('DOCUMENT_NOT_READY: 미완료/저신뢰 문서 또는 호출 중 해석 변경'),
                   **COMMON_ERRORS},
        examples=[
            OpenApiExample('질문', value={'message': '필요한 서류를 확인하고 싶습니다.'}, request_only=True),
            OpenApiExample('정상 질문 저장 (공식 근거 미확인)', value=MESSAGE_EXAMPLE,
                           response_only=True, status_codes=['201']),
            OpenApiExample('미완료 또는 저신뢰 문서', value={
                'code': 'DOCUMENT_NOT_READY',
                'message': '문서 해석이 완료되고 내용을 확인한 후 다시 질문해 주세요.', 'details': {},
            }, response_only=True, status_codes=['409']),
            AUTH_EXAMPLE,
            OpenApiExample('AI 서비스 실패', value={
                'code': 'CHAT_SERVICE_UNAVAILABLE',
                'message': '답변을 생성할 수 없습니다. 잠시 후 다시 시도해 주세요.', 'details': {},
            }, response_only=True, status_codes=['503']),
        ],
    ),
)
class DocumentChatView(generics.GenericAPIView):
    permission_classes = [IsOwner]
    serializer_class = ChatMessageSerializer
    pagination_class = StandardPagination

    def get(self, request, pk):
        document = owned_document(request.user, pk)
        self.check_object_permissions(request, document)
        page = self.paginate_queryset(message_history(document))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def post(self, request, pk):
        message = submit_question(request.user, pk, request.data)
        return Response(self.get_serializer(message).data, status=status.HTTP_201_CREATED)

    def handle_exception(self, exc):
        # 공통 handler는 사용자 정의 409/503의 내용을 의도적으로 마스킹한다.
        # 챗봇의 고정 오류만 같은 공통 JSON 형식으로 반환한다. 임의 예외 내용은 사용하지 않는다.
        if isinstance(exc, DatabaseError):
            exc = ChatServiceUnavailable()
        if isinstance(exc, (ChatServiceUnavailable, DocumentNotReady)):
            return Response({
                'code': exc.default_code, 'message': exc.default_detail, 'details': {},
            }, status=exc.status_code)
        return super().handle_exception(exc)
