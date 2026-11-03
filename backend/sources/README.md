# 공식 행정자료 저장 계약 (P1-6)

`sources`는 사용자 업로드 문서와 분리된 공식자료 저장소다. 관리 API나 수집·임베딩
생성·검색 기능은 포함하지 않는다. 기존 `documents`·인증 모델과 마이그레이션은 변경하지 않는다.

## 모델과 보존 정책

- `SourceDocument`: 기관명(`agency` 문자열), 적용 지역(`region`), 분류, 발행일·확인일과 상태를 저장한다.
  `PENDING`은 확인 대기, `ACTIVE`는 사용 중, `INACTIVE`는 검색 근거에서 제외할 자료다.
  발행일과 확인일은 기본값이 없는 nullable 필드이며 생성 시각으로 채우지 않는다.
  `content_hash`는 선택 입력이며 같은 본문의 서로 다른 URL을 허용한다. 해시 계산은 P1-7 담당이다.
- `SourceChunk`: `document.chunks.all()`로 문서 내 0부터 시작하는 순서대로 조회한다.
  `(source_document, chunk_order)`가 고유하며 문서가 다르면 같은 순서를 사용할 수 있다.
  청크가 연결된 자료 삭제는 `PROTECT`로 거부한다. 갱신·실패·상태 변경에 따른 청크 삭제는 없다.
  향후 근거 참조 모델도 `PROTECT` 등 참조 보존 정책을 정해야 한다. 현재 청크 자체의 ORM 삭제까지
  전역 금지하지는 않으므로 후속 수집 서비스는 기존 청크를 자동 삭제하거나 ID를 재사용하면 안 된다.
- `CrawlLog`: 등록 전 URL도 독립적으로 기록한다. `RUNNING`·`SUCCESS`·`FAILED`는 자료 상태와 별개다.
  `started_at` 기본값은 `timezone.now`, `finished_at`은 실행 중 비워 둘 수 있다.
  동일 URL의 실행마다 새 레코드를 생성하고 과거 이력을 덮어쓰지 않는다.
  대상 URL은 입력 원문을 보존한다. **후속 수집 서비스는 URL·오류 메시지의 비밀번호·API 키·접근
  토큰을 제거한 후 저장해야 한다.** 이 모델 자체는 임의 오류 문자열의 비밀정보를 자동 탐지하지 않는다.

기관·적용 지역·분류에 단일 인덱스, 청크에 FK 인덱스와 문서·순서 고유 제약을 둔다.
로그에는 전체 최신 순과 URL별 최신 순을 위한 인덱스를 둔다. 자료·청크·로그의 PK는 `BigAutoField`다.

## URL 정규화

`sources.fields.normalize_source_url()`과 `NormalizedURLField`가 다음 규칙을 공유한다.

- 앞뒤 공백 제거, scheme·호스트 소문자화, 국제화 호스트의 IDNA 변환
- HTTP 80 / HTTPS 443 기본 포트 제거, 빈 경로를 `/`로 통일, fragment 제거
- HTTP와 HTTPS, 경로 대소문자·마지막 `/`, 쿼리 값과 순서는 구분

리다이렉트·접근 가능성 확인이나 네트워크 요청은 하지 않는다. 사이트별 쿼리 삭제나 경로 병합은
다른 자료를 합칠 수 있어 수행하지 않는다. P1-7은 이 함수를 재사용하고 정규화 URL로 조회·저장한다.
필드 변환은 `full_clean()`, `save()`, 리터럴 값을 사용하는 `bulk_create()`·`bulk_update()`·
`QuerySet.update()`에도 적용된다. SQL 표현식이나 직접 SQL로 URL을 쓰면 변환을 우회할 수 있으므로
수집 서비스에서 사용하지 않는다. 정규화된 저장 URL의 DB 고유 제약이 동시 중복 저장도 차단한다.
이 필드 클래스는 초기 마이그레이션이 참조하므로 후속 변경에서도 import 경로를 보존해야 한다.

## AI 임베딩 계약

최신 dev와 별도 `docs/service-context` 문서를 확인했다. pgvector는 계획만 있으며 패키지·DB 확장·
벡터 차원·모델 계약은 구현되어 있지 않다. 따라서 `embedding`은 양 DB에서 지원하는 nullable
`JSONField`로 준비했다. 기본값은 `None`이며 AI가 생성한 숫자 배열을 저장할 수 있다.
차원이나 JSON 구조는 아직 고정하지 않는다. 이 필드는 벡터 검색 엔진이 아니다.
**pgvector 도입 시 필드 변경 또는 별도 테이블 마이그레이션이 필요할 수 있다.**

## Admin

기존 Django staff 인증과 모델별 권한을 사용한다. 자료 메타데이터·상태는 권한 있는 관리자가
수정할 수 있다. URL은 등록 후 읽기 전용이며 본문 해시·확인일도 읽기 전용이다.
자료 상세에서 청크를 순서대로 확인한다. 청크·실행 로그는 추가·수정·삭제 없이 조회만 제공하며
자료도 Admin에서 삭제할 수 없다. 임베딩은 목록에 표시하지 않는다.

## 검증 경계

`save()`가 `full_clean()`을 자동 호출하지 않는 Django 기본 동작을 유지한다.

| 항목 | 모델·폼 검증 (`full_clean()`) | DB 보장 |
| --- | --- | --- |
| 필수 URL | 빈 값·형식·길이 | NOT NULL·빈 문자열 CHECK |
| 자료 URL 중복 | 정규화 후 고유성 | 정규화된 저장값 UNIQUE |
| 제목·기관명·지역·분류 필수 및 길이 | 검증 | NOT NULL (빈 문자열·길이는 SQLite에서 별도 검증 필요) |
| 자료 상태·실행 결과 | TextChoices | 허용값 CHECK |
| 청크 원본·순서 | 필수·양수 범위·문서별 중복 | FK·NOT NULL·0 이상·문서/순서 UNIQUE |
| 로그 종료 시각 | `finished_at` 필드 오류 | 종료가 NULL이거나 시작 이상인 CHECK |

`backend`에서 프로젝트 가상환경으로 실행한다. 테스트는 독립된 신규 테스트 DB를 사용한다.

```powershell
python manage.py check
python manage.py test sources
python manage.py test
python manage.py makemigrations --check --dry-run
```

마이그레이션은 `sources/0001_initial.py`에 세 모델만 생성한다. SQLite·PostgreSQL 공통 ORM
필드·제약·인덱스만 사용하며 PostgreSQL 확장을 설치하지 않는다.
