from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated, ParseError, ValidationError
from rest_framework.views import exception_handler


ERRORS = {
    400: ('BAD_REQUEST', '요청 내용을 확인해 주세요.'),
    401: ('AUTHENTICATION_FAILED', '인증 정보가 유효하지 않습니다.'),
    403: ('PERMISSION_DENIED', '이 요청을 수행할 권한이 없습니다.'),
    404: ('NOT_FOUND', '요청한 리소스를 찾을 수 없습니다.'),
    405: ('METHOD_NOT_ALLOWED', '지원하지 않는 HTTP 메서드입니다.'),
    406: ('NOT_ACCEPTABLE', '지원하지 않는 응답 형식입니다.'),
    415: ('UNSUPPORTED_MEDIA_TYPE', '지원하지 않는 Content-Type입니다.'),
    429: ('THROTTLED', '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.'),
}


def api_exception_handler(exc, context):
    """DRF가 처리한 예외만 정규화하며 HTTP 상태와 인증·재시도 헤더를 유지한다."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    details = {}
    if isinstance(exc, ValidationError):
        code, message = 'VALIDATION_ERROR', '입력값을 확인해 주세요.'
        # 중첩 딕셔너리·리스트 및 ErrorDetail을 그대로 보존한다.
        details = response.data if isinstance(response.data, dict) else {
            'non_field_errors': response.data,
        }
    elif isinstance(exc, NotAuthenticated):
        code, message = 'NOT_AUTHENTICATED', '인증이 필요합니다.'
    elif isinstance(exc, AuthenticationFailed):
        code, message = 'AUTHENTICATION_FAILED', '인증 정보가 유효하지 않습니다.'
    elif isinstance(exc, ParseError):
        code, message = 'PARSE_ERROR', '요청 본문을 해석할 수 없습니다.'
    else:
        # 인증·권한 예외의 임의 상세 문자열에 내부 정보가 있어도 노출하지 않는다.
        code, message = ERRORS.get(
            response.status_code, ('API_ERROR', '요청을 처리할 수 없습니다.'),
        )
    response.data = {'code': code, 'message': message, 'details': details}
    return response
