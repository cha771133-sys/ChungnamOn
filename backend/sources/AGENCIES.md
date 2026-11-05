# 담당기관 시드와 조회 계약 (P1-9)

## 저장 모델과 식별 규칙

`Agency`는 `SourceDocument`와 관계없이 존재한다. 기관명·부서·지역·연락처·연락처 구분·
공식 출처·실제 확인일·생성/수정 시각을 저장한다. 기관과 부서의 이름은 분리한다.
`phone_type`은 `REPRESENTATIVE`(대표·상담 번호), `DEPARTMENT`(부서 번호), `UNKNOWN`(미확인)이다.
기관 대표번호를 특정 업무 부서 직통번호로 해석하면 안 된다.

`(name, department, region)`이 DB 고유키다. NFKC 유니코드 정규화, 앞뒤 공백 제거,
연속 공백 통일, `ㆍ`·`・`를 중점으로, 일부 하이픈 변형을 `-`로 통일한다.
지역·부서·괄호·영문 대소문자는 보존한다. 기관명의 공백을 모두 제거하거나 `시청`으로 축약하지 않는다.
같은 기관의 다른 부서와 다른 지역의 동명 기관은 서로 다른 행이다.

`AgencyTextField`가 검증·일반 저장·리터럴 bulk 쓰기에 같은 정규화를 적용한다.
DB UNIQUE와 필수 이름·지역 CHECK가 중복과 빈 식별자를 차단한다.
직접 SQL·SQL 표현식으로 식별자를 쓰면 정규화를 우회하므로 사용하지 않는다.
이 필드의 import 경로는 마이그레이션이 참조하므로 보존한다.

`AgencyAlias`는 기관별로 정규화한 별칭이 고유하다. 기관 간 같은 별칭은 허용하되 조회에서 모호함을
반환한다. 공식 페이지의 기관/부서 표기나 합의된 별칭만 관리자가 등록한다.
초기 시드의 `충남도청`, `외국인종합안내센터`는 공식 페이지에 표시된 이름이며,
`천안시청 민원여권과`, `아산시청 징수과`는 같은 출처의 기관과 부서 표기를 조합한 식별명이다.

기관명과 별칭에 인덱스를 두고 SQLite·PostgreSQL 공통 필드와 제약만 사용한다.
연락처·출처·확인일은 nullable이며 확인일 기본값은 없다. 모델 `full_clean()`과 Admin 폼은
연락처에 공식 HTTPS 출처와 시간대가 포함된 과거 확인일을 요구한다. `save()`가 모든 모델 검증을
자동 실행하는 것은 아니므로 외부에서 등록할 때 `full_clean()`을 거쳐야 한다.
Admin은 기존 staff 인증과 모델별 권한을 사용하며 별칭을 인라인으로 관리한다.
일반 사용자에게 생성·수정·삭제 API는 제공하지 않는다.

## 실제 출처 확인 기록

2026-11-05 00:27 KST에 아래 공개 HTML을 제한적으로 요청하여 이름·부서·전화번호를 확인했다.
각 응답을 확인한 실제 UTC 시각을 `data/agencies.yaml`에 고정했다. 단위 테스트는 외부 요청을 하지 않는다.

| 기관 | 부서 / 번호 종류 | 확인 번호 | 공식 출처 |
| --- | --- | --- | --- |
| 충청남도청 | 대표 상담 | 041-120 | [외교부 국내 대행기관 안내](https://www.passport.go.kr/home/kor/substitutional/index.do?idx=&menuPos=28&pageIndex=1&searchKeyword=&searchValue1=0&searchValue2=20&searchValue3=&searchValue4=&tabPos=A) |
| 천안시청 | 민원여권과 / 여권업무 부서 | 041-521-3323 | 위 외교부 안내의 천안시청 행 |
| 아산시청 | 징수과 / 인터넷지방세 안내 부서 | 041-537-3001 | [아산시청 인터넷지방세](https://www.asan.go.kr/main/cms/?no=93) |
| 법무부 | 외국인종합안내센터 / 상담 | 1345 | [법무부 외국인종합안내센터](https://www.moj.go.kr/moj/196/subview.do) |
| 국민건강보험공단 | 외국어 상담 | 033-811-2000 | [공단 2024년 3월 웹진](https://www.nhis.or.kr/static/alim/paper/oldpaper/202403/sub/section4_7.html) |

충남도청 홈페이지는 404, 천안시청 홈페이지는 403으로 확인되어 우회 요청하지 않았다.
대신 외교부의 공개 기관 연락처 안내에서 확인했다. 외교부 안내는 `충남도청` 표기와 주소의
`충청남도청` 표기를 함께 제공한다. `041-120`은 충남콜센터로 명시되어 있어 대표 상담으로 구분했다.
천안시청 번호를 지방세·체류업무 부서로 안내하지 않는다. 외교부 안내의 아산 부서명은 표와 주소에
서로 다른 표기가 있어 시드에 채택하지 않고, 아산시 자체 지방세 안내의 징수과만 채택했다.

아산 페이지의 최종 업데이트일은 2025-09-11이다. 공단 연락처는 2024년 발행 안내의 공개 번호이며,
그 안내의 존재를 확인한 것만으로 2026년 현재 번호·적용조건이 최신이라고 단정하지 않는다.
공단의 기관 명칭은 [공단 홈페이지](https://www.nhis.or.kr/nhis/index.do)에서 별도로 확인했다.
출입국 상담의 해외 번호는 페이지 간 표기가 달라 이번 시드에는 일관되게 표시된 국내 1345만 넣었다.

공식 페이지에 안내된 연락처 사실정보만 선별 기록하고 본문·이미지·서식·직원 개인정보는 복제하지 않았다.
아산 이용약관의 상업적 가공·판매 제한과 법무부 저작권정책의 자료별 이용조건을 유지한다.
이 기록은 페이지 전체의 이용허락이나 상업적 재배포 허가를 의미하지 않는다.
공식자료 원문 수집·청크·임베딩·AI 재가공에는 별도 검토가 필요하다.
기존 `source_urls.yaml`의 `enabled=false` 항목은 근거로 승격하거나 활성화하지 않았다.
외교부 도메인은 기관 안내 URL 검증에만 추가했으며 P1-7 크롤러의 허용 호스트는 변경하지 않았다.

## 시드 명령

프로젝트 가상환경의 `backend/`에서 실행한다. 마이그레이션은 스키마만 추가하며 기관 데이터는
아래 명령으로 별도 등록한다. 운영 DB 적용 전에는 대상 환경을 확인한다.

```powershell
python manage.py migrate
python manage.py seed_agencies --dry-run
python manage.py seed_agencies
# 검토한 별도 YAML을 사용할 때
python manage.py seed_agencies --file path/to/agencies.yaml --dry-run
```

YAML은 최상위 `agencies` 목록에 `name`, `department`, `region`, `phone`, `source_url`,
`checked_at`, `aliases`를 모두 지정한다. `phone_type`은 선택이며 기본은 `UNKNOWN`이다.
연락처 미확인은 `null`, 부서 없음은 빈 문자열, 별칭 없음은 빈 목록으로 쓴다.
`checked_at`은 실제 확인한 ISO 8601 시각과 시간대 또는 `null`이다. 미래·시간대 없는 값은 거부한다.
지역은 기존 수집 목록과 동일하게 충청남도·천안시·아산시·전국을 사용한다.

모든 항목을 먼저 검증하고 정규화 후 중복을 거부한다. DB 반영은 전체 목록을 하나의 트랜잭션으로
처리하고 기존 행을 잠가 갱신한다. 같은 시드의 재실행은 ID·확인일·수정일을 바꾸지 않는다.
현재 확인일보다 **엄격하게 최신인** 시드만 연락처·번호 구분·출처·확인일 묶음을 갱신한다.
확인일이 같으면서 정보가 충돌하면 기존 정보를 보존한다. 새 번호가 비어 있으면 기존 확인된
번호와 그 출처·확인일 묶음을 유지한다. 오래되거나 확인일이 없는 시드는 기존 별칭도 추가하지 않는다.
검증된 동일 정보에 대한 별칭 추가는 가능하다. 기존 별칭과 시드에서 빠진 기관을 자동 삭제하지 않는다.
동시 최초 생성은 DB UNIQUE와 `get_or_create`로 처리하며 부분 갱신 실패 시 이번 실행 전체를 취소한다.
`--dry-run`은 신규·갱신·동일 예정 집계만 출력하고 INSERT·UPDATE·DELETE를 실행하지 않는다.
명령은 외부 사이트를 요청하거나 실행 시각을 확인일로 채우지 않는다.

## 조회 API와 documents 연동

```http
GET /api/sources/agencies/lookup/?name=아산시청&region=아산시
Authorization: Bearer <access-token>
```

`name`은 필수 255자 이하, `region`은 선택 100자 이하다. 빈 값·반복 파라미터·제어/숨은 문자·
URL 등 허용하지 않는 형식은 HTTP 400과 기존 `{code, message, details}` 오류로 반환한다.
JWT 누락·무효는 401, 조회 이외 메서드는 405다. 공식기관 공용정보이므로 `IsOwner`를 적용하지 않는다.
Swagger는 기존 `/api/docs/`, OpenAPI는 `/api/schema/`에서 확인한다.

서비스는 View·DRF·AI에 의존하지 않으며 Django 앱이 초기화된 환경에서 호출한다.

```python
from sources.services.agency_lookup import lookup_agency

result = lookup_agency(name="아산시청", region="아산시")
# result.lookup_status / applicability_status / agency / candidates / message
```

`AgencyLookupResult`는 불변 dataclass다. 잘못된 서비스 입력은 필드별 Django `ValidationError`를
발생시킨다. 공식명으로 먼저 조회하고 없으면 별칭으로 조회한 뒤 지역을 정확히 필터한다.
공식명이 다른 지역에만 있어도 별칭으로 다른 기관을 확정하지 않는다. `전국` 자동 대체나
퍼지 검색·하위 부서 추론은 없다. 부서 구분이 필요하면 등록된 기관+부서 별칭을 전달한다.

| 조회 상태 | agency | candidates | HTTP |
| --- | --- | --- | --- |
| FOUND | 단일 Agency | 빈 tuple / API에서는 빈 배열 | 200 |
| AMBIGUOUS | None | 모든 후보, 이름·지역·부서·ID 순 | 200 |
| UNVERIFIED | None | 빈 tuple / API에서는 빈 배열 | 200 |

API 최상위 필드는 `lookupStatus`, `applicabilityStatus`, `agency`, `candidates`, `message`다.
기관 객체 필드는 `id`, `name`, `department`, `region`, `phone`, `phoneType`, `sourceUrl`, `checkedAt`이다.
빈 연락처·출처·확인일은 `null`이며 미확인 값을 추정하지 않는다. 날짜는 시간대가 있는 ISO 8601이다.
응답 메시지는 확인일 기준 정보의 재확인을 안내한다. 임의의 유효기간 기준이나 자동 최신 판정은 없다.

개인 적용 상태의 어휘는 `CONFIRMED`(확인됨), `CONDITIONAL`(조건부),
`NEEDS_CONFIRMATION`(확인 필요), `MISMATCH`(불일치)다. P1-9의 모든 결과는
`NEEDS_CONFIRMATION`이며 Agency에 저장하지 않는다. 기관 미검색은 불일치 증거가 아니다.
A의 기존 공식 검증 결과를 이 기본값으로 덮어쓰지 말고 별도로 보존한다.
문서 최종 조합·개인 적용 근거·문서 소유권 검사는 documents 담당 A의 책임이다.
이 서비스는 사용자 파일이나 `SourceDocument.agency`로 담당기관을 자동 확정하지 않는다.

## 검증

신규 모델·정규화·시드·정확 일치·모호함·JWT·오류 계약·Admin·Swagger·기존 데이터 보존을
네트워크 없는 테스트에서 확인한다. 이전 sources 마이그레이션은 수정하지 않았다.
기존 마이그레이션 테스트의 종료 시 스키마 복원 대상만 최신 leaf로 변경하여 새 모델 테스트와 격리했다.

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py spectacular --validate --fail-on-warn --file schema.yaml
```
