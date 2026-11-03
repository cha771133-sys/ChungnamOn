from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsOwner
from core.schema import error_response

from .models import Document
from .serializers import (
    DocumentStatusSerializer, DocumentUploadResponseSerializer, DocumentUploadSerializer,
)


@extend_schema_view(post=extend_schema(
    operation_id='document_upload',
    summary='문서 업로드',
    description=(
        '인증한 사용자의 문서를 multipart/form-data로 업로드합니다. '
        'jpg, jpeg, png, pdf를 지원하며 최대 20MB입니다. '
        '소유자는 서버에서 지정하고 PENDING 상태로 응답합니다.'
    ),
    request={'multipart/form-data': DocumentUploadSerializer},
    responses={
        201: DocumentUploadResponseSerializer,
        400: error_response('VALIDATION_ERROR: 파일 누락, 지원하지 않는 확장자 또는 20MB 초과'),
        403: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED / PERMISSION_DENIED: 현재 인증 정책에 따른 접근 거부'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        415: error_response('UNSUPPORTED_MEDIA_TYPE: 지원하지 않는 Content-Type'),
    },
    examples=[
        OpenApiExample('업로드 완료', value={'documentId': 123, 'status': 'PENDING'},
                       response_only=True, status_codes=['201']),
        OpenApiExample('파일 검증 오류', value={
            'code': 'VALIDATION_ERROR', 'message': '입력값을 확인해 주세요.',
            'details': {'file': ['jpg, jpeg, png, pdf 파일만 업로드할 수 있습니다.']},
        }, response_only=True, status_codes=['400']),
    ],
    tags=['문서'],
))
class DocumentUploadView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = DocumentUploadSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.save(owner=request.user, step=Document.Step.QUEUED)
        # OCR·해석 처리는 2단계에서 비동기로 연결한다.
        return Response(
            {'documentId': document.id, 'status': document.status},
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(get=extend_schema(
    operation_id='document_status',
    summary='내 문서 처리 상태 조회',
    description='현재 사용자 소유 문서의 상태와 진행 단계를 조회합니다. 타인 문서와 없는 문서는 동일하게 404를 반환합니다.',
    parameters=[OpenApiParameter('pk', int, OpenApiParameter.PATH, description='문서 ID')],
    responses={
        200: DocumentStatusSerializer,
        403: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED / PERMISSION_DENIED: 현재 인증 정책에 따른 접근 거부'),
        404: error_response('NOT_FOUND: 접근 가능한 문서가 없음'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
    },
    examples=[OpenApiExample('대기 중인 문서', value={
        'documentId': 123, 'status': 'PENDING', 'step': 'QUEUED',
    }, response_only=True)],
    tags=['문서'],
))
class DocumentStatusView(generics.RetrieveAPIView):
    permission_classes = [IsOwner]
    serializer_class = DocumentStatusSerializer

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user)
