# P0-1 인증·프로필 — 이슈 #14

## API 계약

| 메서드 / 경로 | 입력 | 성공 응답 | 인증 |
| --- | --- | --- | --- |
| POST `/api/auth/register/` | `email`, `password` | 201 `{id, email}` | 공개 |
| POST `/api/auth/login/` | `email`, `password` | 200 `{access, refresh}` | 공개 |
| POST `/api/auth/token/refresh/` | `refresh` | 200 `{access, refresh}` | Refresh Token |
| POST `/api/auth/logout/` | `refresh` | 200 `{}` | Refresh Token |
| GET `/api/auth/me/` | 없음 | 200 아래 프로필 형식 | Bearer Access Token |
| PATCH `/api/auth/me/` | `language`, `nationality`, `visaType` | 200 아래 프로필 형식 | Bearer Access Token |

```json
{"id":1,"email":"user@example.com","language":"vi","nationality":"VN","visaType":"E-9","onboardingCompleted":true}
```

- 회원가입은 프로필이나 토큰을 생성하지 않는다. 이메일 앞뒤 공백을 제거하고 전체를 소문자로 저장하며, 로그인과 관리자 로그인에도 같은 정책을 적용한다. DB에는 이메일 고유 제약과 `LOWER(TRIM(email))` 고유 제약을 둔다.
- 프로필이 없으면 세 프로필 필드는 `null`, `onboardingCompleted`는 `false`다. 조회로 프로필 행을 만들지 않는다.
- 최초 설정 또는 미완료 프로필에는 세 필드를 모두 제출해야 한다. 유효성 검사에 실패하면 일부 필드도 저장하지 않는다. 완료 후 부분 수정은 가능하지만 빈 값·`null`로 필드를 지울 수 없다.
- 언어: `vi`, `en`, `zh`, `uz`, `ne`, `th`, `ko`. 국적: 대문자 영문 두 자리 코드 형식. 체류자격: `E-9`, `H-2`, `F-4`, `F-6`, `D-2`, `D-4`, `E-7`, `F-2`, `F-5`, `ETC`. 프론트 목업의 언어·체류자격 선택지를 기준으로 했다. 국적·체류자격 정보는 안내용이며 실제 국적이나 체류자격을 검증하지 않는다.
- 국적 ‘기타’는 대문자 `ZZ`로 전송·저장·조회한다. `ZZ`는 서비스 내부에서 ‘기타’를 뜻하는 코드이며 특정 국가에 부여된 ISO 3166-1 alpha-2 코드가 아니다. 최초 설정에도 사용할 수 있으며 다른 두 항목이 유효하면 `onboardingCompleted`는 `true`다. 설정 완료 후 `VN` 등 국가 코드와 `ZZ` 사이의 부분 수정도 가능하다. 소문자 `zz`, 빈 값, `null`은 허용하지 않는다.
- 프로필 수정은 위 세 필드만 허용한다. 사용자 ID 경로는 제공하지 않으며 요청 본문으로 대상 사용자나 계정·권한 정보를 지정할 수 없다.
- 유효성 오류는 400 `{code: "VALIDATION_ERROR", message, details: {필드: [오류]}}`, 인증 누락은 401 `NOT_AUTHENTICATED`, 인증 실패·무효/만료 토큰은 401 `AUTHENTICATION_FAILED`다. 모두 공통 `code/message/details` 형식과 기존 HTTP 상태·인증 헤더를 유지한다. 잘못된 비밀번호, 미등록 계정, 비활성 계정은 동일한 로그인 오류를 반환한다.
- Access Token은 15분, Refresh Token은 7일이다. 갱신하면 새 Refresh Token도 반환하고 이전 토큰은 blacklist에 넣는다. 프론트는 두 토큰을 함께 교체해야 한다.
- 로그아웃은 제출한 Refresh Token만 무효화한다. 중복·무효 로그아웃은 401이다. **기존 Access Token은 만료까지 최대 15분간 유효할 수 있다.** 프론트에서도 보관한 토큰을 제거해야 한다. 다른 로그인 세션의 Refresh Token은 유지된다.
- 요청 본문·비밀번호·토큰을 애플리케이션 로그에 기록하지 않는다. 배포 시 `DJANGO_DEBUG=False`와 충분히 긴 무작위 `DJANGO_SECRET_KEY`를 사용한다. SimpleJWT blacklist는 패키지 기본 동작에 따라 Refresh Token을 DB에 보관하므로 DB와 백업도 민감 데이터로 취급한다.
- `/api/health/`는 토큰 없이 접근 가능하다. P0-5 통합(#20)에서 인증·프로필·문서 명세는 `/api/schema/`, Swagger UI는 `/api/docs/`로 제공한다. 문서 소유자 필터, 성공·처리 중 응답과 기존 마이그레이션은 유지한다.

## 기존 DB 전환 차단 사항

2026-11-03 작업 전 로컬 SQLite를 읽기 전용으로 점검했다.

- 기본 `auth.User` 사용자 0명, 사용자-그룹·직접 권한 관계 0개, 그룹·관리자 로그·세션 0개.
- `auth`, `admin`, `contenttypes`, `sessions` 마이그레이션 적용됨.
- `documents.0001_initial` 미적용, `documents_document` 테이블 없음.

사용자가 없어도 `AUTH_USER_MODEL`을 바꾸면 이미 적용된 `admin.0001_initial`의 동적 의존성이 `accounts.0001_initial`을 가리킨다. 따라서 기존 DB에 그대로 `migrate`하면 `InconsistentMigrationHistory`로 중단된다. 기존 documents 마이그레이션까지 적용된 DB도 같은 의존성 검토가 필요하며, 실제 FK는 여전히 `auth_user`를 가리킬 수 있다.

**이 PR은 기존 DB의 자동 전환을 구현하지 않는다. 기존 로컬 DB는 변경하지 않았으며, 기존/배포 DB의 전환을 결정·검증하기 전에는 배포 완료로 간주할 수 없다.** DB 삭제·초기화, 과거 마이그레이션 수정, `--fake` / `--fake-initial`은 사용하지 않았다. Railway DB의 데이터와 적용 이력은 접근·점검하지 않았으므로 신규 테스트 DB의 성공으로 운영 안전성을 주장하지 않는다.

## 데이터 보존 전환안 — 별도 합의 및 리허설 필요

기존 DB의 이력을 억지로 변경하지 않도록, 별도 신규 DB로의 논리적 이관을 제안한다. 아래는 아직 실행하지 않은 전환안이다.

1. 운영 담당자·A 담당자와 대상 DB 및 전환 시간을 합의한다. 사용자·문서·모든 사용자 FK·그룹/권한·관리자 로그·세션과 적용 이력을 조사하고, DB 및 문서 원본 저장소의 복구 가능한 백업을 만든다.
2. 기존 사용자 이메일의 누락, 잘못된 형식, 정규화 후 중복을 조사한다. 임의 이메일 생성, 계정 병합·삭제는 하지 않고 충돌 해결을 담당자에게 확정받는다. 문제가 남아 있으면 전환을 중단한다.
3. 분리된 빈 DB에 현재 코드의 마이그레이션을 정상 적용한다. `accounts.User`는 반드시 `accounts/0001_initial.py`에서 생성된다. 기존 `django_migrations` 테이블은 복사하지 않는다.
4. 별도 검토한 이관 도구로 사용자 PK·기존 비밀번호 해시·활성/관리자 상태·가입일을 보존하고 이메일만 합의된 정책으로 정규화한다. 해시를 `set_password`로 다시 해싱하지 않는다. 문서와 다른 앱 데이터의 사용자 참조 및 파일 경로를 보존한다. 그룹·권한·content type·관리자 로그의 ID 매핑과 기존 세션 처리 정책도 명시적으로 검증한다.
5. 복제 환경에서 건수·주요 값·관계·FK 무결성·권한·관리자 로그인·문서 접근·파일 원본·전체 테스트를 대조한다. 실제 PostgreSQL에서 이관과 롤백을 리허설한다. 신규 SQLite 테스트만으로 이를 대체하지 않는다.
6. 쓰기 중지 및 최종 동기화 후 검증을 통과하면 담당자가 연결 설정을 신규 DB로 전환한다. 기존 DB와 백업은 보존하며, 재개 후 쓰기까지 고려한 롤백 정책을 합의한다. 이번 작업에서는 연결 전환도 수행하지 않는다.

## 검증 기록

- 기준 `origin/dev`: `a614205`. Python 3.14.5 / Django 6.1.2 / DRF 3.18.3 / SimpleJWT 5.5.1.
- 변경 전 `check`, 기존 13개 테스트, `showmigrations` 통과.
- 변경 후 `check`, `makemigrations --check --dry-run`, 전체 54개 테스트(신규 41개 + 기존 13개)가 통과했다. 사용자·관리자, 회원가입, JWT rotation/blacklist/만료·변조·비활성/삭제 계정, 프로필·권한, 기존 문서 소유자 관계를 검증했다. 기존 테스트의 회귀는 없다.
- 검증 명령은 `backend`에서 실행한다: `python manage.py check`, `python manage.py test`, `python manage.py makemigrations --check --dry-run`. 기존 DB의 이력 문제와 독립적으로 검사하려면 해당 프로세스에만 `DATABASE_URL=sqlite:///:memory:`를 지정한다. 실제 `.env`나 기존 DB 설정은 덮어쓰지 않는다.
- 신규 파일 SQLite에서 `migrate --noinput`, `showmigrations`, `migrate --check`가 모두 통과했다. accounts·documents·blacklist 적용, DB 무결성, 문서·관리자 로그·토큰의 `accounts_user` FK를 확인했다.
- 기존 DB의 `migrate --plan`은 예상한 `InconsistentMigrationHistory`로 실패했다. 전후 파일 해시가 같아 기존 DB가 보존됨을 확인했다. 검증 도구의 임시 SQLite 정리에서 발생한 Windows 파일 잠금은 연결을 명시적으로 닫아 해결했으며, 임시 파일도 정리했다.
- 작업 전에도 있던 `backend/staticfiles` 미생성 경고는 배포의 `collectstatic` 단계와 관련된다. 로컬의 짧은 `.env` 서명키 경고도 확인했으며, 최종 테스트는 임시 무작위 키로 실행한다. 실제 `.env`는 수정하지 않는다.

## 국적 기타 코드 — 이슈 #52

- [x] 이메일+비밀번호 회원가입·로그인 API 회귀 확인
- [x] simplejwt 토큰 발급·재발급·로그아웃 회귀 확인
- [x] 프로필 언어·국적·체류자격 세 항목 필수 및 저장·부분 수정 확인
- [x] 비로그인 사용자 차단 회귀 확인
- [x] 국적 `ZZ`의 최초 설정·재조회·국가 코드와의 상호 수정 확인
- [x] 소문자 `zz` 거절 및 기존 빈 값·`null` 검증 유지 확인
- [x] Swagger 입력·응답과 프론트 주석에 `ZZ`의 의미 명시

프로필 모델의 `help_text`를 반영하는 `0002_document_other_nationality_code` 마이그레이션을 포함한다. 국적 값과 검증 규칙은 변경하지 않는다.

검증: `check`, `makemigrations --check --dry-run`, 관련 테스트 60개 통과. 임시 파일 SQLite에서 전체 577개 테스트가 통과했다(PostgreSQL 전용 6개 제외). 프론트 `npm run lint`는 오류 없이 기존 경고 7개, `npm run build`는 통과했다.
