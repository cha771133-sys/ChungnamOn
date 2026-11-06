import logging

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardPagination
from core.permissions import IsOwner
from core.schema import error_response

from .models import Document, InterpretationResult
from .serializers import (
    DocumentDetailSerializer, DocumentListSerializer, DocumentNotReadySerializer,
    DocumentResultSerializer, DocumentStatusResponseSerializer, DocumentStatusSerializer,
    DocumentUploadResponseSerializer, DocumentUploadSerializer,
)
from .services.runner import start_processing

logger = logging.getLogger(__name__)

DOCUMENT_EXAMPLE = {
    'documentId': 123, 'title': '주민세 납부 안내', 'docType': '고지서',
    'fileName': 'notice.jpg', 'fileType': 'IMAGE', 'status': 'DONE', 'step': 'DONE',
    'lowConfidence': False, 'createdAt': '2026-11-05T09:00:00+09:00',
    'expiresAt': '2026-12-05T09:00:00+09:00',
}
PK_PARAMETER = OpenApiParameter('pk', int, OpenApiParameter.PATH, description='문서 ID')


@extend_schema_view(get=extend_schema(
    operation_id='document_list',
    summary='내 문서함 목록 조회',
    description=(
        '현재 사용자가 올린 문서를 최신순(createdAt 내림차순, ID 내림차순)으로 반환합니다. '
        '기본 20개·최대 100개입니다. status로 처리 상태를 걸러 볼 수 있습니다. '
        '해석 결과 본문은 결과 조회 API에서 받습니다.'
    ),
    parameters=[
        OpenApiParameter('status', str, enum=Document.Status.values, description='처리 상태 필터'),
        OpenApiParameter('page', int, description='페이지 번호'),
        OpenApiParameter('page_size', int, description='기본 20, 최대 100'),
    ],
    responses={
        200: DocumentListSerializer(many=True),
        400: error_response('VALIDATION_ERROR: 지원하지 않는 status 값'),
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
        404: error_response('NOT_FOUND: 없는 페이지'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
    },
    examples=[
        OpenApiExample('내 문서 목록', value={
            'count': 1, 'next': None, 'previous': None, 'results': [DOCUMENT_EXAMPLE],
        }, response_only=True, status_codes=['200']),
        OpenApiExample('status 검증 오류', value={
            'code': 'VALIDATION_ERROR', 'message': '입력값을 확인해 주세요.',
            'details': {'status': ['PENDING, PROCESSING, DONE, FAILED 중 하나를 입력하세요.']},
        }, response_only=True, status_codes=['400']),
    ],
    tags=['문서'],
), post=extend_schema(
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
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
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
class DocumentListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = StandardPagination

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return DocumentUploadSerializer
        return DocumentListSerializer

    def get_queryset(self):
        queryset = Document.objects.filter(owner=self.request.user).order_by('-created_at', '-id')
        status_filter = self.request.query_params.get('status')
        if status_filter is None:
            return queryset
        if status_filter not in Document.Status.values:
            raise ValidationError({'status': [f'{", ".join(Document.Status.values)} 중 하나를 입력하세요.']})
        return queryset.filter(status=status_filter)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.save(owner=request.user, step=Document.Step.QUEUED)
        # 저장이 커밋된 뒤에 시작해야 백그라운드 스레드가 문서를 읽을 수 있다.
        transaction.on_commit(lambda: start_processing(document.id))
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
        200: DocumentStatusResponseSerializer,
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
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


@extend_schema_view(
    get=extend_schema(
        operation_id='document_detail',
        summary='내 문서 상세 조회',
        description=(
            '현재 사용자 소유 문서의 정보와 처리 상태를 반환합니다. '
            'hasResult가 true면 결과 조회 API로 해석 결과를 받을 수 있습니다. '
            '타인 문서와 없는 문서는 동일하게 404를 반환합니다.'
        ),
        parameters=[PK_PARAMETER],
        responses={
            200: DocumentDetailSerializer,
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
            404: error_response('NOT_FOUND: 접근 가능한 문서가 없음'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        },
        examples=[
            OpenApiExample('해석 완료 문서', value={
                **DOCUMENT_EXAMPLE, 'failReason': '', 'maskedCount': 2, 'hasResult': True,
            }, response_only=True, status_codes=['200']),
            OpenApiExample('처리 실패 문서', value={
                **DOCUMENT_EXAMPLE, 'title': '', 'docType': '', 'status': 'FAILED', 'step': 'OCR',
                'failReason': '문서의 글자를 읽지 못했습니다. 밝은 곳에서 다시 촬영해 업로드해 주세요.',
                'maskedCount': 0, 'hasResult': False,
            }, response_only=True, status_codes=['200']),
        ],
        tags=['문서'],
    ),
    delete=extend_schema(
        operation_id='document_delete',
        summary='내 문서 삭제',
        description=(
            '현재 사용자 소유 문서와 해석 결과·챗봇 대화, 저장된 원본 파일을 삭제합니다. '
            'PROCESSING 문서는 처리가 끝난 뒤 삭제할 수 있으며 결과 조회 API와 같은 형식의 409를 반환합니다. '
            '타인 문서와 없는 문서는 동일하게 404를 반환합니다.'
        ),
        parameters=[PK_PARAMETER],
        responses={
            204: None,
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
            404: error_response('NOT_FOUND: 접근 가능한 문서가 없음'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
            409: DocumentNotReadySerializer,
        },
        examples=[OpenApiExample('처리 중인 문서', value={
            'detail': '처리 중인 문서는 삭제할 수 없습니다.', 'status': 'PROCESSING', 'step': 'OCR',
        }, response_only=True, status_codes=['409'])],
        tags=['문서'],
    ),
)
class DocumentDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [IsOwner]
    serializer_class = DocumentDetailSerializer

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user).select_related('result')

    def destroy(self, request, *args, **kwargs):
        document = self.get_object()
        if document.status == Document.Status.PROCESSING:
            return self._processing_response(document)

        # 조회 뒤 처리가 시작됐을 수 있어 행을 잠근 채 상태를 다시 확인하고 지운다.
        # 잠금 중에는 process_document의 선점 update가 대기하고, 삭제 후에는 선점에 실패한다.
        with transaction.atomic():
            current = Document.objects.select_for_update().filter(pk=document.pk).first()
            if current is None:
                raise Http404
            if current.status == Document.Status.PROCESSING:
                return self._processing_response(current)
            current.delete()

        try:
            document.file.delete(save=False)
        except OSError:
            # DB 삭제는 끝났으므로 파일 정리 실패는 기록만 하고 응답은 성공으로 둔다.
            logger.exception('문서 파일 삭제 실패: document_id=%s file=%s', document.pk, document.file.name)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _processing_response(self, document):
        return Response(
            {'detail': '처리 중인 문서는 삭제할 수 없습니다.', 'status': document.status, 'step': document.step},
            status=status.HTTP_409_CONFLICT,
        )


@extend_schema_view(get=extend_schema(
    operation_id='document_result',
    summary='내 문서 해석 결과 조회',
    description=(
        '현재 사용자 소유의 DONE 문서 해석 결과를 반환합니다. '
        '처리 중이거나 실패하면 기존 상태·단계·실패 사유를 포함한 409 응답을 반환합니다. '
        '타인 문서와 없는 문서는 동일하게 404를 반환합니다.'
    ),
    parameters=[OpenApiParameter('pk', int, OpenApiParameter.PATH, description='문서 ID')],
    responses={
        200: DocumentResultSerializer,
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
        404: error_response('NOT_FOUND: 접근 가능한 문서 또는 결과가 없음'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        409: DocumentNotReadySerializer,
    },
    tags=['문서'],
))
class DocumentResultView(APIView):
    permission_classes = [IsOwner]

    def get(self, request, pk):
        document = get_object_or_404(Document, pk=pk, owner=request.user)
        self.check_object_permissions(request, document)
        if document.status != Document.Status.DONE:
            return Response(self._not_ready_body(document), status=status.HTTP_409_CONFLICT)

        result = get_object_or_404(InterpretationResult.objects.select_related('document'), document=document)
        return Response(DocumentResultSerializer(result).data)

    def _not_ready_body(self, document):
        if document.status == Document.Status.FAILED:
            return {
                'detail': '문서 처리에 실패했습니다.',
                'status': document.status,
                'step': document.step,
                'failReason': document.fail_reason,
            }
        return {'detail': '아직 처리 중입니다.', 'status': document.status, 'step': document.step}
