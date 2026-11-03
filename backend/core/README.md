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
회원가입·프로필 검증도 같은 계약을 사용합니다. JWT 인증 누락·실패는 401과
`WWW-Authenticate: Bearer realm="api"`를 유지하고, 인증 후 객체 권한 거부는 403입니다.
소유자 필터에 걸린 타인 문서는 존재하지 않는 문서와 같은 `NOT_FOUND` 404입니다.

문서 결과 조회가 직접 반환하는 처리 중·실패 409는 기존 업무 응답인
`{detail, status, step, failReason?}`를 보존합니다. 이는 예외 처리기가 정규화하는
오류와 구분되며 OpenAPI에도 별도 응답으로 명시합니다.

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
- 문서화 대상: Health, 회원가입·로그인·JWT 갱신·로그아웃, 내 프로필 조회·수정, 문서 업로드·상태·결과 조회
- 업로드: `multipart/form-data`의 `file`, jpg/jpeg/png/pdf, 최대 20MB
- Swagger UI 정적 리소스는 drf-spectacular 기본 CDN을 사용하므로 브라우저에서 인터넷 접근이 필요합니다.

`drf-spectacular==0.30.0`으로 고정했으며 기존 Django 6.1.2 / DRF 3.18.3 환경에서
의존성 검사, 명세 검증과 API 회귀 테스트를 통과했습니다.

### P0-1 통합 완료 — 이슈 #20

기존 PR #17 브랜치에 `origin/dev`의 P0-1(#15)과 문서 비동기 처리·결과 조회(#13)를
일반 merge로 통합했습니다. `AUTH_USER_MODEL=accounts.User`, 전역 JWT 인증 및
`IsAuthenticated` 권한을 유지하면서 공통 예외 처리·페이지네이션·OpenAPI 설정을 합쳤습니다.

Access 15분·Refresh 7일, Refresh 회전 및 이전 토큰 blacklist 정책은 변경하지 않았습니다.
회원가입·로그인·갱신·로그아웃과 Health·Swagger는 기존 공개 정책을 유지합니다.
프로필·문서 API는 실제 Bearer JWT 보안 스키마와 401 오류 응답을 문서화합니다.
프로필 PATCH 입력은 `language`, `nationality`, `visaType`만 포함하며 최초 세 항목 필수
검증과 설정 완료 후 부분 수정 정책을 유지합니다.

문서 업로드의 커밋 후 처리 실행, 상태의 선택적 `failReason`, 해석 결과 및 409 응답을
보존했습니다. 상태·결과 조회 모두 소유자 필터와 `IsOwner`를 적용합니다. 모델·서비스·
마이그레이션은 최신 dev와 같습니다. 운영용 목록·수정·삭제 API는 없으므로 공통 권한
단위 테스트와 실제 JWT를 사용하는 격리된 목록 뷰로 권한·페이지네이션을 검증합니다.

통합 후 격리된 메모리 SQLite DB에서 전체 115개 테스트, 시스템 검사, 마이그레이션 누락
검사, 경고를 오류로 처리한 OpenAPI 생성·검증, 패키지 의존성 검사를 통과했습니다.
기존 로컬 DB·운영 DB는 변경하지 않았습니다. 사용자 모델 전환에 따른 기존 DB 이관
위험은 [accounts 문서](../accounts/README.md)의 별도 전환 검토 사항으로 남습니다.

## 검증 명령

`backend`에서 프로젝트 가상환경으로 실행합니다. 테스트에는 별도 테스트 DB를 사용합니다.

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py spectacular --validate --fail-on-warn --file schema.yaml
python -m pip check
```

생성 명세는 검증용 산출물이며 저장소에 커밋할 필요가 없습니다. DB 모델·마이그레이션은 변경하지 않습니다.
