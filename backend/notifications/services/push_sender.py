"""Firebase 호출은 이 모듈에 격리한다. 작업 점유·재시도·이력 저장은 호출자의 책임이다."""

import json
import threading
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.parse import quote

import firebase_admin
import httpx
import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from firebase_admin import credentials, exceptions, messaging
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError

APP_NAME = 'chungnamon-notifications'
_initialization_lock = threading.Lock()


class PushStatus(StrEnum):
    ACCEPTED = 'ACCEPTED'
    INVALID_DEVICE = 'INVALID_DEVICE'
    CONFIGURATION_ERROR = 'CONFIGURATION_ERROR'
    RETRYABLE = 'RETRYABLE'
    PERMANENT_ERROR = 'PERMANENT_ERROR'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class PushResult:
    status: PushStatus
    error_code: str = ''
    provider_message_id: str = ''


@sensitive_variables()
def get_firebase_app():
    """요청/앱 시작 때는 인증하지 않고 실제 발송 시 전용 앱을 한 번 초기화한다."""
    with _initialization_lock:
        try:
            return firebase_admin.get_app(APP_NAME)
        except ValueError:
            raw = settings.FIREBASE_SERVICE_ACCOUNT_JSON
            credential = credentials.Certificate(json.loads(raw)) if raw else credentials.ApplicationDefault()
            return firebase_admin.initialize_app(credential, {'httpTimeout': 10}, name=APP_NAME)


@sensitive_variables()
def build_deadline_message(*, registration_id, schedule_id, deadline_date, days_before, document_id=None):
    """검증된 DB 식별자를 받을 내부 함수. 원문 업무명 대신 일반 업무명만 노출한다."""
    if days_before not in (7, 1) or type(days_before) is not int or type(deadline_date) is not date:
        raise ValueError('마감일 또는 사전 알림 일수가 올바르지 않습니다.')
    identifier = str(schedule_id)
    if not identifier or len(identifier) > 128:
        raise ValueError('일정 식별자가 올바르지 않습니다.')
    data = {
        'type': 'DEADLINE_REMINDER', 'scheduleId': identifier, 'daysBefore': str(days_before),
        'deadlineDate': deadline_date.isoformat(), 'url': f'/?schedule={quote(identifier, safe="")}',
    }
    if document_id is not None:
        data['documentId'] = str(document_id)
    body = (
        '등록한 일정의 마감일이 7일 남았습니다.'
        if days_before == 7 else '등록한 일정의 마감일이 내일입니다.'
    )
    return messaging.Message(
        fid=registration_id,
        notification=messaging.Notification(
            title='ChungnamOn 마감 알림',
            body=body,
        ),
        data=data,
    )


@sensitive_variables()
def send_deadline_push(**message_fields):
    """한 번의 SDK 호출 결과만 반환한다. 성공은 FCM 접수이며 수신/읽음이 아니다.

    앱 자체 재시도는 없다. SDK 내부 전송 재시도는 존재하므로 정확히 한 번을 보장하지 않는다.
    타임아웃·서비스 장애는 UNKNOWN으로 남기고 자동 재전송하면 안 된다.
    """
    try:
        message = build_deadline_message(**message_fields)
    except (TypeError, ValueError):
        return PushResult(PushStatus.PERMANENT_ERROR, 'INVALID_PAYLOAD')
    try:
        app = get_firebase_app()
        if not app.project_id:
            return PushResult(PushStatus.CONFIGURATION_ERROR, 'FIREBASE_CONFIGURATION')
    except TransportError:
        return PushResult(PushStatus.UNKNOWN, 'NETWORK_RESULT_UNKNOWN')
    except (DefaultCredentialsError, RefreshError, ValueError, TypeError, KeyError, OSError):
        return PushResult(PushStatus.CONFIGURATION_ERROR, 'FIREBASE_CONFIGURATION')
    try:
        message_id = messaging.send(message, app=app)
        if not isinstance(message_id, str) or not message_id:
            return PushResult(PushStatus.UNKNOWN, 'INVALID_PROVIDER_RESPONSE')
        return PushResult(PushStatus.ACCEPTED, provider_message_id=message_id)
    except messaging.UnregisteredError:
        return PushResult(PushStatus.INVALID_DEVICE, 'UNREGISTERED')
    except (exceptions.UnauthenticatedError, exceptions.PermissionDeniedError,
            DefaultCredentialsError, RefreshError):
        # SenderIdMismatch/ThirdPartyAuth도 운영 프로젝트·APNs/VAPID 설정 문제로 분류한다.
        return PushResult(PushStatus.CONFIGURATION_ERROR, 'FIREBASE_AUTHENTICATION')
    except exceptions.ResourceExhaustedError:
        return PushResult(PushStatus.RETRYABLE, 'QUOTA_EXCEEDED')
    except (exceptions.InvalidArgumentError, ValueError):
        # SDK 입력 검증 실패도 확정된 인자 오류다. 기기를 임의로 무효화하지 않는다.
        return PushResult(PushStatus.PERMANENT_ERROR, 'INVALID_ARGUMENT')
    except (exceptions.UnavailableError, exceptions.InternalError):
        return PushResult(PushStatus.UNKNOWN, 'SERVICE_UNAVAILABLE')
    except (exceptions.DeadlineExceededError, requests.RequestException, httpx.HTTPError, TransportError):
        return PushResult(PushStatus.UNKNOWN, 'NETWORK_RESULT_UNKNOWN')
    except Exception:
        # 예외 문자열은 FID/비밀정보를 포함할 수 있어 결과와 로그에 복사하지 않는다.
        return PushResult(PushStatus.UNKNOWN, 'UNEXPECTED_PROVIDER_ERROR')
