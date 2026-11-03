# 공통 API 기반 (P0-5)

## 소유권 권한

`core.permissions.IsOwner`는 인증된 사용자와 객체의 `owner`가 같을 때만 허용합니다.
소유자가 없거나 관계가 해소되지 않으면 거부하며, 관리자도 소유권을 우회하지 않습니다.
문서 상태 조회는 기존 소유자 queryset 필터를 유지하면서 객체 권한도 확인합니다.
타인 문서와 없는 문서는 모두 같은 404 응답입니다.

객체 권한은 목록과 생성 요청의 소유권을 자동으로 보장하지 않습니다. 새 목록 API는
`get_queryset()`에서 `owner=request.user`로 제한하고, 생성 API는
`serializer.save(owner=request.user)`로 소유자를 지정해야 합니다. 연결 객체를 입력받는
API는 해당 외래키의 queryset도 현재 사용자가 접근 가능한 대상으로 제한해야 합니다.
현재 운영 목록 API와 연결 객체 입력 API는 없으며 테스트 전용 뷰만 사용합니다.

## 오류 응답

DRF 기본 예외 처리기가 처리한 오류에만 다음 형식을 적용합니다. HTTP 상태 코드와
`WWW-Authenticate`, `Retry-After` 헤더는 유지합니다. 성공 응답과 Django HTML 응답은 유지합니다.

```json
{
  "code": "VALIDATION_ERROR",
  "message": "입력값을 확인해 주세요.",
  "details": {"file": ["파일은 최대 20MB까지 업로드할 수 있습니다."]}
}
```

검증 오류의 중첩 딕셔너리·리스트와 모든 메시지는 `details`에 보존합니다.
최상위 목록 오류는 `details.non_field_errors`에 넣으며, 추가 정보가 없는 일반 오류는
`details: {}`입니다. 인증·권한 등의 오류는 예외 원문 대신 고정 안내 문구를 사용합니다.
처리되지 않은 예외는 기존 Django 오류 처리로 전달하며 정상 응답으로 변환하지 않습니다.

| 오류 | code | 상태 |
| --- | --- | --- |
| 입력 검증 | VALIDATION_ERROR | 400 |
| 본문 해석 | PARSE_ERROR | 400 |
| 인증 누락 | NOT_AUTHENTICATED | 인증 정책에 따른 401/403 |
| 인증 실패 | AUTHENTICATION_FAILED | 인증 정책에 따른 401/403 |
| 권한 없음 | PERMISSION_DENIED | 403 |
| 객체/페이지 없음 | NOT_FOUND | 404 |
| 메서드 미지원 | METHOD_NOT_ALLOWED | 405 |
| 응답 형식 미지원 | NOT_ACCEPTABLE | 406 |
| Content-Type 미지원 | UNSUPPORTED_MEDIA_TYPE | 415 |
| 요청 제한 | THROTTLED | 429 |

그 밖에 DRF가 처리한 예외는 상태에 맞는 일반 코드 또는 `API_ERROR`를 반환합니다.
프론트엔드는 기존 최상위 필드 오류 대신 `details`에서 검증 메시지를 읽어야 합니다.

## 페이지네이션

`core.pagination.StandardPagination`을 DRF 전역 기본값으로 등록합니다.
기본 20개, `page_size`로 최대 100개를 요청할 수 있으며 페이지 파라미터는 `page`입니다.
응답은 DRF 표준 `count`, `next`, `previous`, `results`이고 없는 페이지는 404입니다.
0·음수·숫자가 아닌 `page_size`는 DRF 정책에 따라 기본 크기로 처리합니다.

목록 뷰는 소유자 필터 후 고유 키를 포함한 명시적 정렬(예: `order_by('-created_at', '-pk')`)을
지정해야 동일 시각 데이터에서도 페이지 순서가 안정적입니다. 페이지 번호 방식은 페이지
요청 사이의 삽입·삭제에 대한 스냅샷을 보장하지 않습니다. 현재 업로드·상태 조회는 단건
API이므로 페이지네이션을 적용하지 않습니다.

## OpenAPI와 Swagger

- `/api/schema/`: OpenAPI 3.0.3 명세 (YAML 기본, JSON은 `?format=json`)
- `/api/docs/`: 공개 Swagger UI
- 문서화 대상: Health, 문서 업로드, 내 문서 상태 조회
- 업로드: `multipart/form-data`의 `file`, jpg/jpeg/png/pdf, 최대 20MB
- Swagger UI 정적 리소스는 drf-spectacular 기본 CDN을 사용하므로 브라우저에서 인터넷 접근이 필요합니다.

`drf-spectacular==0.30.0`으로 고정했으며 기존 Django 6.1.2 / DRF 3.18.3 환경에서
의존성 검사, 명세 검증과 API 회귀 테스트를 통과했습니다.

### P0-1 통합 시 확인할 사항

작업 기준 `dev`에는 accounts 앱과 JWT 로그인 API가 없습니다. Session/Basic 기본 인증을
보존하고 이를 실제 명세에 표시합니다. 인증 누락은 현재 403이며 JWT 로그인 경로를 만들지 않습니다.

P0-1 병합 시 기존 JWT·권한 설정과 이번 `REST_FRAMEWORK` 항목을 병합해야 합니다.
drf-spectacular의 SimpleJWT 자동 연동은 JWTAuthentication을 사용하는 뷰에 Bearer 보안
스키마와 Swagger Authorize를 제공합니다. 테스트에서 Bearer 스키마 및 유효/무효 토큰
호환성을 별도로 검증합니다. 병합 후 실제 인증·프로필 API의 요청/성공 응답을 명시하고,
문서 API의 인증 오류 명세에 401을 추가해야 합니다. accounts의 검증 오류 테스트도
`details` 계약으로 갱신하고 토큰 회전·로그아웃·프로필 회귀 테스트를 실행해야 합니다.

현재 회원가입·로그인·프로필 회귀 검증은 선행 기능이 미병합이므로 실행 대상이 아닙니다.
운영용 목록·수정·삭제 API도 없어 공통 권한 단위 테스트와 격리된 목록 뷰로 검증합니다.

## 검증 명령

`backend`에서 프로젝트 가상환경으로 실행합니다. 테스트에는 별도 테스트 DB를 사용합니다.

```powershell
python manage.py check
python manage.py test
python manage.py spectacular --validate --fail-on-warn --file schema.yaml
pip check
```

생성 명세는 검증용 산출물이며 저장소에 커밋할 필요가 없습니다. DB 모델·마이그레이션은 변경하지 않습니다.
