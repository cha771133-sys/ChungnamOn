"""문서 접근, 문맥, AI 출력 검증 및 성공한 질문·답변 쌍 저장."""

from django.conf import settings
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, NotAuthenticated

from accounts.models import UserProfile
from documents.models import Document, InterpretationResult
from sources.models import SourceDocument
from sources.services.errors import CollectionError
from sources.services.source_registry import validate_source_url

from ..models import ChatMessage
from ..serializers import QuestionSerializer
from . import ai_client

HISTORY_LIMIT = 10
INTERPRETATION_FIELDS = (
    'original_text', 'translated_text', 'summary', 'terms', 'deadlines',
    'required_docs', 'agency', 'verification', 'language',
)


class ChatServiceUnavailable(APIException):
    status_code = 503
    default_code = 'CHAT_SERVICE_UNAVAILABLE'
    default_detail = '답변을 생성할 수 없습니다. 잠시 후 다시 시도해 주세요.'


class DocumentNotReady(APIException):
    status_code = 409
    default_code = 'DOCUMENT_NOT_READY'
    default_detail = '문서 해석이 완료되고 내용을 확인한 후 다시 질문해 주세요.'


def owned_document(user, document_id, *, for_update=False):
    if not user or not user.is_authenticated:
        raise NotAuthenticated()
    queryset = Document.objects.filter(owner=user)
    if for_update:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, pk=document_id)


def message_history(document):
    return ChatMessage.objects.filter(document_id=document.pk).order_by('-created_at', '-id')


def _ensure_ready(document):
    if document.status != Document.Status.DONE or document.low_confidence:
        raise DocumentNotReady()


def _interpretation(document, *, for_update=False):
    queryset = InterpretationResult.objects.filter(document=document)
    if for_update:
        queryset = queryset.select_for_update()
    result = queryset.values('id', *INTERPRETATION_FIELDS).first()
    if result is None:
        raise ChatServiceUnavailable()
    return result


def _language(user, interpretation):
    profile_language = UserProfile.objects.filter(user=user).values_list('language', flat=True).first()
    # Django 기본 언어 ko-kr와 지원 코드 ko를 대응한다.
    for value in (profile_language, interpretation['language'], settings.LANGUAGE_CODE.split('-')[0]):
        if value in UserProfile.Language.values:
            return value
    return 'ko'


def _official_sources(interpretation, *, for_update=False):
    verification = interpretation.get('verification')
    if not isinstance(verification, dict) or verification.get('status') not in ('MATCHED', 'MISMATCH'):
        return []
    references = verification.get('sources', [])
    if not isinstance(references, list):
        return []
    # 기존 더미 검증에는 ID가 없다. URL/기관명만으로 연결 관계를 추측하지 않는다.
    ids = {entry.get('sourceDocumentId') for entry in references if isinstance(entry, dict)
           and type(entry.get('sourceDocumentId')) is int and entry['sourceDocumentId'] > 0}
    queryset = SourceDocument.objects.filter(
        pk__in=ids, status=SourceDocument.Status.ACTIVE, collected_at__isnull=False,
    ).only('id', 'title', 'source_url', 'collected_at', 'content_hash').order_by('id')
    if for_update:
        queryset = queryset.select_for_update()
    sources = []
    for source in queryset:
        try:
            validate_source_url(source.source_url)
        except CollectionError:
            continue
        sources.append({
            'sourceDocumentId': source.pk, 'title': source.title, 'url': source.source_url,
            'checkedAt': source.collected_at.isoformat(), 'contentHash': source.content_hash,
        })
    return sources


def _validate_answer(payload, language, official_sources, interpretation):
    if not isinstance(payload, dict):
        raise ai_client.AIClientError()
    required = {'answer', 'sources', 'grounded', 'suggestedQuestions'}
    if not required.issubset(payload):
        raise ai_client.AIClientError()
    answer, sources = payload['answer'], payload['sources']
    grounded, suggestions = payload['grounded'], payload['suggestedQuestions']
    status = payload.get('status', 'GROUNDED' if grounded is True else 'UNVERIFIED')
    if (not isinstance(answer, str) or not answer.strip() or len(answer) > 20000
            or type(grounded) is not bool or not isinstance(sources, list) or len(sources) > 20
            or status not in ChatMessage.Status.values
            or grounded != (status == ChatMessage.Status.GROUNDED)
            or payload.get('language', language) != language
            or not isinstance(suggestions, list) or len(suggestions) > 5
            or any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in suggestions)):
        raise ai_client.AIClientError()
    known = {source['sourceDocumentId']: source for source in official_sources}
    snapshots = []
    for source in sources:
        if not isinstance(source, dict) or type(source.get('sourceDocumentId')) is not int:
            raise ai_client.AIClientError()
        canonical = known.get(source['sourceDocumentId'])
        if canonical is None:
            raise ai_client.AIClientError()
        # AI가 URL/제목/확인일을 새로 만들거나 변경할 수 없다. ID만 반환해도 된다.
        if any(source[key] != canonical[key] for key in canonical if key in source):
            raise ai_client.AIClientError()
        if canonical not in snapshots:
            snapshots.append(canonical)
    if status in (ChatMessage.Status.GROUNDED, ChatMessage.Status.MISMATCH):
        if not snapshots or 'status' not in payload:
            raise ai_client.AIClientError()
        if status == ChatMessage.Status.MISMATCH and interpretation['verification'].get('status') != 'MISMATCH':
            raise ai_client.AIClientError()
    return {
        'answer': answer.strip(), 'language': language, 'sources': snapshots,
        'grounded': grounded, 'status': status,
        'suggested_questions': list(dict.fromkeys(q.strip() for q in suggestions)),
    }


def submit_question(user, document_id, data):
    try:
        return _submit_question(user, document_id, data)
    except (ai_client.AIClientError, DatabaseError, ModelValidationError):
        raise ChatServiceUnavailable() from None


def _submit_question(user, document_id, data):
    document = owned_document(user, document_id)
    _ensure_ready(document)
    interpretation = _interpretation(document)
    serializer = QuestionSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    question = serializer.validated_data['message']
    language = _language(user, interpretation)
    sources = _official_sources(interpretation)
    history = list(message_history(document).values(
        'question', 'answer', 'language', 'grounded', 'status',
    )[:HISTORY_LIMIT])
    history.reverse()
    # 미확인 답변을 공식 근거 입력과 분리하고 파일/마스킹 전 OCR은 포함하지 않는다.
    payload = ai_client.prepare_ai_payload({
        'question': question, 'interpretation': {key: interpretation[key] for key in INTERPRETATION_FIELDS},
        'history': history, 'language': language,
    })
    try:
        response = ai_client.generate_answer(**payload, official_sources=sources)
    except Exception:
        # 외부 모듈의 임의 예외 문자열에는 질문·문서·인증정보가 포함될 수 있다.
        raise ChatServiceUnavailable() from None
    answer = _validate_answer(response, language, sources, interpretation)
    # AI 호출 동안 DB 잠금을 유지하지 않는다. 저장 직전에 상태/해석/출처를 다시 검사한다.
    with transaction.atomic():
        current = owned_document(user, document_id, for_update=True)
        _ensure_ready(current)
        if _interpretation(current, for_update=True) != interpretation:
            raise DocumentNotReady()
        if _official_sources(interpretation, for_update=True) != sources:
            raise ChatServiceUnavailable()
        return ChatMessage.objects.create(document=current, question=question, **answer)
