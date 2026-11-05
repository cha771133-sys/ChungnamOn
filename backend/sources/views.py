from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import error_response
from sources.serializers import AgencyLookupQuerySerializer, AgencyLookupResponseSerializer
from sources.services.agency_lookup import lookup_agency


class AgencyLookupView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id='agency_lookup', summary='발신기관 이름으로 담당기관 조회', tags=['담당기관'],
        description=(
            'JWT Bearer Access Token이 필요합니다. 공식명 우선, 등록 별칭의 정확 일치만 사용합니다. '
            'FOUND는 단일 기관 식별, AMBIGUOUS는 복수 후보, UNVERIFIED는 미확인입니다. 모두 HTTP 200입니다. '
            '개인 적용 상태는 CONFIRMED(확인됨), CONDITIONAL(조건부), NEEDS_CONFIRMATION(확인 필요), '
            'MISMATCH(불일치)와 구분합니다. 이 API는 항상 NEEDS_CONFIRMATION을 반환합니다. '
            'region은 정확 일치 필터이며 전국으로 대체하지 않습니다. 확인일은 과거 확인 시점이며 현재 유효성을 보장하지 않습니다.'
        ),
        parameters=[AgencyLookupQuerySerializer],
        responses={
            200: AgencyLookupResponseSerializer,
            400: error_response('VALIDATION_ERROR: name 누락·빈 값·255자 초과·잘못된 형식, region 빈 값·100자 초과'),
            401: error_response('NOT_AUTHENTICATED / AUTHENTICATION_FAILED: JWT 누락 또는 무효'),
            405: error_response('METHOD_NOT_ALLOWED: 조회 이외의 HTTP 메서드'),
        },
        examples=[
            OpenApiExample('단일 기관 식별', response_only=True, value={
                'lookupStatus': 'FOUND', 'applicabilityStatus': 'NEEDS_CONFIRMATION',
                'agency': {'id': 1, 'name': '가상시청', 'department': '', 'region': '가상시',
                           'phone': None, 'phoneType': 'UNKNOWN', 'sourceUrl': None, 'checkedAt': None},
                'candidates': [], 'message': '구조 설명용 가상 예시입니다. 개인별 적용 여부는 담당기관에 확인해야 합니다.',
            }),
            OpenApiExample('기관 미확인', response_only=True, value={
                'lookupStatus': 'UNVERIFIED', 'applicabilityStatus': 'NEEDS_CONFIRMATION',
                'agency': None, 'candidates': [], 'message': '담당기관 정보를 확인할 수 없습니다. 문서 발신기관에 직접 확인해 주세요.',
            }),
            OpenApiExample('중복 후보', response_only=True, value={
                'lookupStatus': 'AMBIGUOUS', 'applicabilityStatus': 'NEEDS_CONFIRMATION', 'agency': None,
                'candidates': [
                    {'id': 1, 'name': '가상시청', 'department': '가상부서', 'region': '가상시',
                     'phone': None, 'phoneType': 'UNKNOWN', 'sourceUrl': None, 'checkedAt': None},
                    {'id': 2, 'name': '가상시청', 'department': '다른가상부서', 'region': '가상시',
                     'phone': None, 'phoneType': 'UNKNOWN', 'sourceUrl': None, 'checkedAt': None},
                ], 'message': '여러 담당기관 후보가 있습니다. 지역·담당 부서를 추가로 확인해 주세요.',
            }),
            OpenApiExample('잘못된 입력', response_only=True, status_codes=['400'], value={
                'code': 'VALIDATION_ERROR', 'message': '입력값을 확인해 주세요.',
                'details': {'name': ['이 필드는 필수 항목입니다.']},
            }),
        ],
    )
    def get(self, request):
        for field in ('name', 'region'):
            if len(request.query_params.getlist(field)) > 1:
                raise ValidationError({field: ['같은 파라미터는 한 번만 입력해 주세요.']})
        # QueryDict의 HTML 폼 해석은 선택 필드의 빈 값을 누락으로 간주하므로 명시적으로 보존한다.
        query = AgencyLookupQuerySerializer(data=request.query_params.dict())
        query.is_valid(raise_exception=True)
        result = lookup_agency(**query.validated_data)
        return Response(AgencyLookupResponseSerializer(result).data)
