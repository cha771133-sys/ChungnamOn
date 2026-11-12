import logging

from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
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

from .models import Document, InterpretationResult, Schedule
from .serializers import (
    DocumentDetailSerializer, DocumentListSerializer, DocumentNotReadySerializer,
    DocumentResultSerializer, DocumentStatusResponseSerializer, DocumentStatusSerializer,
    DocumentUploadResponseSerializer, DocumentUploadSerializer, ScheduleConflictSerializer,
    ScheduleCreateSerializer, ScheduleSerializer, ScheduleUpdateSerializer, VerificationSerializer,
    normalize_verification,
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


VERIFICATION_EXAMPLE = {
    'overall': 'MISMATCH',
    'status': 'MISMATCH',
    'items': [
        {
            'key': 'AMOUNT', 'label': '납부 금액', 'docValue': '6,250원', 'officialValue': '6,250원',
            'status': 'MATCHED', 'reason': '공식 자료의 개인분 주민세 금액과 같아요.', 'sourceIds': ['s1'],
        },
        {
            'key': 'AGENCY_CONTACT', 'label': '문의 연락처', 'docValue': '041-000-0000',
            'officialValue': '041-000-1234', 'status': 'MISMATCH',
            'reason': '공식 자료의 세무과 연락처와 달라요. 전화 전에 공식 번호를 확인하세요.', 'sourceIds': ['s2'],
        },
    ],
    'sources': [
        {
            'id': 's1', 'title': '아산시 지방세 납부 안내', 'url': 'https://www.asan.go.kr/',
            'checkedAt': '2026-10-30', 'publishedAt': '2026-07-01', 'sourceDocumentId': 12,
        },
        {'id': 's2', 'title': '아산시청 부서별 연락처', 'url': 'https://www.asan.go.kr/', 'checkedAt': '2026-10-30'},
    ],
}
LEGACY_VERIFICATION_EXAMPLE = {
    'overall': 'MATCHED', 'status': 'MATCHED', 'items': [],
    'sources': [{'id': 's1', 'title': '아산시 지방세 납부 안내', 'url': 'https://www.asan.go.kr/',
                 'checkedAt': '2026-10-30'}],
}
NOT_READY_EXAMPLES = [
    OpenApiExample('처리 중인 문서', value={
        'detail': '아직 처리 중입니다.', 'status': 'PROCESSING', 'step': 'VERIFYING',
    }, response_only=True, status_codes=['409']),
    OpenApiExample('처리 실패 문서', value={
        'detail': '문서 처리에 실패했습니다.', 'status': 'FAILED', 'step': 'OCR',
        'failReason': '문서의 글자를 읽지 못했습니다. 밝은 곳에서 다시 촬영해 업로드해 주세요.',
    }, response_only=True, status_codes=['409']),
]


def get_done_result(request, view, pk):
    """본인 DONE 문서의 해석 결과를 찾는다. 처리 전·실패 문서면 (None, 409 응답)을 돌려준다."""
    document = get_object_or_404(Document, pk=pk, owner=request.user)
    view.check_object_permissions(request, document)
    if document.status != Document.Status.DONE:
        return None, Response(not_ready_body(document), status=status.HTTP_409_CONFLICT)
    result = get_object_or_404(InterpretationResult.objects.select_related('document'), document=document)
    return result, None


def not_ready_body(document):
    if document.status == Document.Status.FAILED:
        return {
            'detail': '문서 처리에 실패했습니다.',
            'status': document.status,
            'step': document.step,
            'failReason': document.fail_reason,
        }
    return {'detail': '아직 처리 중입니다.', 'status': document.status, 'step': document.step}


@extend_schema_view(get=extend_schema(
    operation_id='document_result',
    summary='내 문서 해석 결과 조회',
    description=(
        '현재 사용자 소유의 DONE 문서 해석 결과를 반환합니다. '
        'verification은 항목별 대조 형식(overall·status·items·sources)이며, '
        '예전 형식으로 저장된 결과는 overall=status, items=[]로 맞추고 출처에 s1, s2… id를 붙여 반환합니다. '
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
    examples=NOT_READY_EXAMPLES,
    tags=['문서'],
))
class DocumentResultView(APIView):
    permission_classes = [IsOwner]

    def get(self, request, pk):
        result, not_ready = get_done_result(request, self, pk)
        if not_ready:
            return not_ready
        return Response(DocumentResultSerializer(result).data)


@extend_schema_view(get=extend_schema(
    operation_id='document_verification',
    summary='내 문서 검증 결과·근거 조회',
    description=(
        '해석 결과의 공식 자료 대조 결과만 반환합니다 (근거 화면용). '
        'items의 sourceIds는 sources[].id를 가리킵니다. 문서에 값이 없는 항목은 items에서 빠집니다. '
        '예전 형식으로 저장된 결과는 overall=status, items=[]로 반환합니다. '
        '처리 중이거나 실패하면 결과 조회 API와 같은 409를 반환하고, 타인 문서와 없는 문서는 동일하게 404를 반환합니다.'
    ),
    parameters=[PK_PARAMETER],
    responses={
        200: VerificationSerializer,
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
        404: error_response('NOT_FOUND: 접근 가능한 문서 또는 결과가 없음'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        409: DocumentNotReadySerializer,
    },
    examples=[
        OpenApiExample('항목별 대조 결과', value=VERIFICATION_EXAMPLE, response_only=True, status_codes=['200']),
        OpenApiExample('예전 형식 결과', value=LEGACY_VERIFICATION_EXAMPLE, response_only=True, status_codes=['200']),
        *NOT_READY_EXAMPLES,
    ],
    tags=['문서'],
))
class DocumentVerificationView(APIView):
    permission_classes = [IsOwner]

    def get(self, request, pk):
        result, not_ready = get_done_result(request, self, pk)
        if not_ready:
            return not_ready
        return Response(normalize_verification(result.verification))


SCHEDULE_EXAMPLE = {
    'scheduleId': 7, 'documentId': 123, 'documentTitle': '주민세 납부 안내',
    'title': '납부 마감 - 주민세 납부 안내', 'date': '2026-08-31', 'done': False, 'doneAt': None,
    'isOverdue': False, 'daysLeft': 12,
    'agency': {'name': '아산시청 세무과', 'phone': '041-000-0000', 'source': 'document'},
    'createdAt': '2026-08-19T09:00:00+09:00', 'updatedAt': '2026-08-19T09:00:00+09:00',
}
SCHEDULE_PK_PARAMETER = OpenApiParameter('pk', int, OpenApiParameter.PATH, description='일정 ID')
BOOLEAN_VALUES = {'true': True, 'false': False}


def owned_schedules(user):
    """사용자의 일정. 문서 소유자가 바뀌면 이전 소유자가 문서 정보를 계속 보지 않도록 문서 소유자도 확인한다."""
    return Schedule.objects.filter(owner=user, document__owner=user).select_related('document', 'document__result')


def parse_boolean_param(params, name):
    """true/false 쿼리 값을 bool로 바꾼다. 없으면 None, 그 외 값은 400."""
    value = params.get(name)
    if value is None:
        return None
    if value.lower() not in BOOLEAN_VALUES:
        raise ValidationError({name: ['true 또는 false를 입력하세요.']})
    return BOOLEAN_VALUES[value.lower()]


@extend_schema_view(get=extend_schema(
    operation_id='schedule_list',
    summary='내 일정 목록 조회',
    description=(
        '현재 사용자의 일정을 마감일이 가까운 순(date 오름차순, ID 오름차순)으로 반환합니다. '
        '기본 20개·최대 100개입니다. isOverdue·daysLeft는 한국 날짜 기준입니다. '
        'overdue=true는 미완료이면서 마감일이 오늘보다 이전인 일정만, overdue=false는 그 외 일정만 반환합니다.'
    ),
    parameters=[
        OpenApiParameter('done', bool, description='완료 여부 필터 (true/false)'),
        OpenApiParameter('overdue', bool, description='마감 경과 필터 (true/false)'),
        OpenApiParameter('page', int, description='페이지 번호'),
        OpenApiParameter('page_size', int, description='기본 20, 최대 100'),
    ],
    responses={
        200: ScheduleSerializer(many=True),
        400: error_response('VALIDATION_ERROR: done·overdue 값이 true/false가 아님'),
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
        404: error_response('NOT_FOUND: 없는 페이지'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
    },
    examples=[
        OpenApiExample('내 일정 목록', value={
            'count': 1, 'next': None, 'previous': None, 'results': [SCHEDULE_EXAMPLE],
        }, response_only=True, status_codes=['200']),
        OpenApiExample('필터 검증 오류', value={
            'code': 'VALIDATION_ERROR', 'message': '입력값을 확인해 주세요.',
            'details': {'done': ['true 또는 false를 입력하세요.']},
        }, response_only=True, status_codes=['400']),
    ],
    tags=['일정'],
), post=extend_schema(
    operation_id='schedule_create',
    summary='일정 등록',
    description=(
        '해석이 끝난(DONE) 본인 문서의 마감일 하나를 일정으로 등록합니다. '
        'date는 결과 조회 API deadlines의 date 중 하나여야 합니다. '
        'title을 비우면 "마감 항목 label - 문서 제목"으로 만듭니다. '
        '같은 문서·날짜·업무명 일정이 이미 있으면 409와 기존 scheduleId를 반환합니다. '
        '타인 문서와 없는 문서는 동일하게 404를 반환합니다.'
    ),
    request=ScheduleCreateSerializer,
    responses={
        201: ScheduleSerializer,
        400: error_response('VALIDATION_ERROR: 필드 누락, 처리 전 문서, deadlines에 없는 날짜'),
        401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
        403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
        404: error_response('NOT_FOUND: 접근 가능한 문서가 없음'),
        405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        409: ScheduleConflictSerializer,
    },
    examples=[
        OpenApiExample('마감일 등록', value={'documentId': 123, 'date': '2026-08-31'}, request_only=True),
        OpenApiExample('등록 완료', value=SCHEDULE_EXAMPLE, response_only=True, status_codes=['201']),
        OpenApiExample('처리 전 문서', value={
            'code': 'VALIDATION_ERROR', 'message': '입력값을 확인해 주세요.',
            'details': {'documentId': ['해석이 완료된 문서만 일정으로 등록할 수 있습니다.']},
        }, response_only=True, status_codes=['400']),
        OpenApiExample('중복 일정', value={'detail': '이미 등록된 일정입니다.', 'scheduleId': 7},
                       response_only=True, status_codes=['409']),
    ],
    tags=['일정'],
))
class ScheduleListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ScheduleCreateSerializer
        return ScheduleSerializer

    def get_queryset(self):
        queryset = owned_schedules(self.request.user).order_by('date', 'id')
        done = parse_boolean_param(self.request.query_params, 'done')
        overdue = parse_boolean_param(self.request.query_params, 'overdue')
        if done is not None:
            queryset = queryset.filter(done=done)
        if overdue is not None:
            overdue_filter = {'done': False, 'date__lt': timezone.localdate()}
            queryset = queryset.filter(**overdue_filter) if overdue else queryset.exclude(**overdue_filter)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = {'owner': request.user, **serializer.validated_data}
        existing = Schedule.objects.filter(**fields).first()
        if existing is None:
            try:
                with transaction.atomic():
                    schedule = Schedule.objects.create(**fields)
            except IntegrityError:
                # 동시에 같은 일정을 등록해 고유 제약에 걸리면 먼저 저장된 일정으로 409를 돌려준다.
                existing = Schedule.objects.filter(**fields).first()
                if existing is None:
                    raise
            else:
                return Response(ScheduleSerializer(schedule).data, status=status.HTTP_201_CREATED)
        return Response(
            {'detail': '이미 등록된 일정입니다.', 'scheduleId': existing.id},
            status=status.HTTP_409_CONFLICT,
        )


@extend_schema_view(
    get=extend_schema(
        operation_id='schedule_detail',
        summary='내 일정 상세 조회',
        description='현재 사용자 소유 일정을 반환합니다. 타인 일정과 없는 일정은 동일하게 404를 반환합니다.',
        parameters=[SCHEDULE_PK_PARAMETER],
        responses={
            200: ScheduleSerializer,
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
            404: error_response('NOT_FOUND: 접근 가능한 일정이 없음'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        },
        examples=[OpenApiExample('미완료 일정', value=SCHEDULE_EXAMPLE, response_only=True, status_codes=['200'])],
        tags=['일정'],
    ),
    patch=extend_schema(
        operation_id='schedule_update',
        summary='일정 완료 처리',
        description=(
            'done만 수정할 수 있습니다. true면 완료 시각(doneAt)을 기록하고, '
            '이미 완료된 일정이면 처음 완료 시각을 유지합니다. false면 doneAt을 비웁니다. '
            '다른 필드를 보내면 400을 반환합니다.'
        ),
        parameters=[SCHEDULE_PK_PARAMETER],
        request=ScheduleUpdateSerializer,
        responses={
            200: ScheduleSerializer,
            400: error_response('VALIDATION_ERROR: done 누락·형식 오류 또는 수정할 수 없는 필드'),
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
            404: error_response('NOT_FOUND: 접근 가능한 일정이 없음'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        },
        examples=[
            OpenApiExample('완료 처리', value={'done': True}, request_only=True),
            OpenApiExample('완료된 일정', value={
                **SCHEDULE_EXAMPLE, 'done': True, 'doneAt': '2026-08-20T10:30:00+09:00',
                'updatedAt': '2026-08-20T10:30:00+09:00',
            }, response_only=True, status_codes=['200']),
            OpenApiExample('수정할 수 없는 필드', value={
                'code': 'VALIDATION_ERROR', 'message': '입력값을 확인해 주세요.',
                'details': {'date': ['수정할 수 없는 필드입니다.']},
            }, response_only=True, status_codes=['400']),
        ],
        tags=['일정'],
    ),
    delete=extend_schema(
        operation_id='schedule_delete',
        summary='내 일정 삭제',
        description='현재 사용자 소유 일정을 삭제합니다. 문서는 그대로 둡니다. 타인 일정과 없는 일정은 동일하게 404를 반환합니다.',
        parameters=[SCHEDULE_PK_PARAMETER],
        responses={
            204: None,
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: Bearer Access Token 누락 또는 무효'),
            403: error_response('PERMISSION_DENIED: 접근 권한 없음'),
            404: error_response('NOT_FOUND: 접근 가능한 일정이 없음'),
            405: error_response('METHOD_NOT_ALLOWED: 지원하지 않는 HTTP 메서드'),
        },
        tags=['일정'],
    ),
)
class ScheduleDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsOwner]
    serializer_class = ScheduleSerializer
    # 수정은 완료 여부만 바꾸는 PATCH로 제한한다.
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return owned_schedules(self.request.user)

    def update(self, request, *args, **kwargs):
        schedule = self.get_object()
        serializer = ScheduleUpdateSerializer(schedule, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ScheduleSerializer(schedule).data)
