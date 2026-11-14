# 공식자료 수집·TXT 수동 등록·검토 운영

## 공식 TXT 수동 등록 (P1, #79)

robots.txt 등으로 자동 수집하기 어려운 공식자료를 **합법적으로 별도 확보한 TXT**로 등록한다.
`import_official_source_txt`는 파일만 읽으며 원본 URL·이용조건 URL에 접속하지 않는다.
공식 도메인은 출처 허용 범위만 확인한다. TXT와 공식 원문의 일치·현재 유효성·AI 가공 허용은
등록 담당자가 확인 자료를 준비하고 최종적으로 관리자가 직접 검토한다.

### 파일 준비와 이용조건 확인

- 로컬 `backend/sources/data/manual/` 폴더를 만든 뒤 UTF-8 또는 UTF-8 BOM `.txt`를 둔다.
  파일은 BOM 포함 **2 MiB 이하**여야 한다. 빈 본문·잘못된 인코딩·바이너리·제어 문자는 거부한다.
  BOM만 본문에서 제외하고 공백·줄바꿈·문구는 그대로 보존한다. 원본 파일은 수정하지 않는다.
- 상대 경로는 `backend/` 기준이며, 지정 폴더 안의 절대 경로도 허용한다. 폴더 밖 경로,
  `..`, 심볼릭 링크·Windows junction, UNC·장치 경로·대체 데이터 스트림은 거부한다.
  테스트는 `SOURCE_TXT_IMPORT_ROOT` 설정을 임시 폴더로 바꾸어 사용한다. 공통 설정 변경은 없다.
- 원본 상세 URL과 이용조건 URL은 기존 `source_registry.OFFICIAL_HOSTS`의 정확한 공식 호스트,
  HTTPS·기본 포트만 허용한다. 인증정보·키·토큰이 있는 URL은 거부한다.
  새 기관 도메인은 별도 코드 리뷰로 허용 목록에 추가해야 한다.
- 원본에서 적용 라이선스·출처표시·상업 이용·변경/가공·재배포 조건과 별도 허가 범위를 직접 확인한다.
  단순 열람·다운로드 허용을 AI 정제·청크·임베딩·검색 허용으로 간주하지 않는다.
  확인 근거를 `--terms-note`에 쓰고 확인한 공식 정책 링크가 있으면 `--terms-url`로 남긴다.
- `--terms-status unverified`가 기본이다. AI 가공이 불명확하면 그대로 등록하여 검토 대기한다.
  `confirmed`는 위 AI 이용이 허용된다는 근거와 확인일이 있을 때만 사용한다.
  가공 금지는 `prohibited`로 기록한다. `unverified`와 `prohibited`는 관리자 승인도 차단한다.
  이용조건이 확정되면 같은 파일을 올바른 조건으로 다시 등록하여 기존 대기 후보를 갱신한다.
- `source_urls.yaml`의 `enabled: false` 자료는 등록자의 `confirmed` 주장만으로 승인할 수 없다.
  특히 **아산 주민세 변경금지 공지**와 공공누리 변경금지 자료를 이 명령으로 강제 활성화하지 않는다.
  별도 이용 허락·정책 검토와 제외 목록의 코드 리뷰가 먼저 필요하다.

### PowerShell 실행 예시와 인자

아래 예시는 로컬 SQLite를 명시한다. 실제로 확보한 파일·공식 상세 주소·기관·제목으로 바꾸어
실행한다. 예시의 `test-fixtures` 주소는 명령 형식을 보여 주는 가상 경로이며 공식 문서가 아니다.
이 명령에는 dry-run 옵션이 없으며 실행하면 로컬 DB에 자료와 검토 후보가 저장된다.

```powershell
Set-Location backend
$env:DATABASE_URL = "sqlite:///db.sqlite3"
New-Item -ItemType Directory -Force "sources/data/manual" | Out-Null
# 합법적으로 확보한 official.txt를 위 폴더에 복사한 뒤 실행
python manage.py import_official_source_txt `
  --file "sources/data/manual/official.txt" `
  --url "https://www.immigration.go.kr/test-fixtures/official-document" `
  --agency "법무부" `
  --title "확보한 공식자료의 실제 제목" `
  --region "전국" `
  --category "출입국·체류" `
  --registered-by "등록 담당자 식별명" `
  --terms-status "unverified" `
  --terms-note "적용 이용조건과 AI 가공 허용 범위를 확인 중"
```

| 인자 | 필수·기본값 | 의미 |
| --- | --- | --- |
| `--file` | 필수 | 허용 폴더 안의 TXT |
| `--url` | 필수 | 공식 원본 상세 HTTPS URL, 정규화한 URL이 HTML 자료 식별자 |
| `--agency`, `--title` | 필수 | 공식 기관명(255자 이하)·문서 제목(500자 이하) |
| `--region` | 기본 `전국` | `충청남도`, `천안시`, `아산시`, `전국` |
| `--category` | 필수 | `지방세`, `건강보험`, `출입국·체류` |
| `--published-at` | 생략 가능 | 확인된 발행일 또는 기준일 `YYYY-MM-DD`, 미상은 생략 |
| `--registered-by` | 필수 | 실제 등록자를 추적할 식별명(255자 이하), 로그인 인증을 대신하지 않음 |
| `--terms-status` | 기본 `unverified` | `unverified`, `confirmed`, `prohibited` |
| `--terms-note` | 필수 | 원본 조건과 AI 가공 허용 근거 또는 미확인 사유(4,000자 이하, 한 줄) |
| `--terms-url` | 생략 가능 | 확인한 공식 이용조건 URL |
| `--terms-checked-on` | `confirmed`일 때 필수 | 실제 조건 확인일 `YYYY-MM-DD`, 미래 날짜 금지 |

허용 범위를 확인한 자료는 같은 명령의 조건 인자를 다음과 같이 바꾸고 다시 실행한다.
아래 문구는 실제 근거로 대체해야 하며 명령 인자만으로 허가를 취득하지 않는다.

```powershell
# 위 명령의 --terms-status, --terms-note를 교체하고 확인일을 추가
# --terms-status "confirmed" `
# --terms-note "실제 적용 라이선스/별도 허가와 AI 가공·검색 허용 범위, 준수할 조건" `
# --terms-checked-on "2026-11-14"
```

### 저장·중복·충돌 처리

1. 파일·경로·인코딩·공식 URL·메타데이터를 검증한다. 실패하면 DB에 자료/후보를 만들지 않는다.
2. 기존 `OfficialRecord`로 본문 UTF-8 바이트의 SHA-256을 계산한다. BOM은 본문 해시에서 제외한다.
   별도로 원본 파일 전체 바이트의 SHA-256·파일명·크기·인코딩·등록자를
   `metadata.manual_import`에, 이용조건과 확인자·확인일을 `metadata.usage_terms`에 기록한다.
   등록 시각은 `SourceRevision.created_at`, 실제 파일 확인 시각은 후보의 `collected_at`이다.
   호스트의 절대 파일 경로나 파일 자체를 DB에 저장하지 않는다.
3. 수집기와 공통인 `stage_official_record`의 트랜잭션·문서 잠금으로 저장한다.
   신규 `SourceDocument`는 본문/해시가 빈 `PENDING / PENDING_REVIEW`이고 TXT는
   `SourceRevision(PENDING_REVIEW)`에만 들어간다. 새 수집 대상·스케줄·외부 요청을 만들지 않는다.
4. 기존 HTML은 정규화한 공식 URL로 연결한다. API 자료와 URL이 겹쳐 식별자가 충돌하면 전체를 취소한다.
   기존 승인 본문·해시·메타데이터·버전·ACTIVE/INACTIVE·청크는 후보 등록으로 바꾸지 않는다.
5. 같은 파일·입력 메타데이터를 반복 등록하면 같은 대기 후보를 재사용한다. 같은 승인 본문과
   메타데이터는 확인일만 갱신한다. 본문이 바뀌면 새 후보를 만들며, 같은 본문이어도 제목·조건·
   등록자·파일 지문 등 메타데이터가 달라지면 검토한다. 메타데이터만 승인하면 AI 재처리를
   불필요하게 다시 요구하지 않는다. 같은 본문의 대기 후보는 새 메타데이터와 확인일로 갱신한다.
6. 승인은 기존 `review_revision`이 승인 버전·이전 해시·최신 관측 해시/확인일을 다시 검사한다.
   오래된 후보·중복 승인·이전 관리 화면의 저장은 충돌로 거부한다. 최신 후보를 다시 확인한다.
   DB 저장 충돌은 종료 코드 1과 `IMPORT_DB_FAILED`로 알리며, 상태 확인 후 같은 명령을 재시도한다.
   파일 오류도 종료 코드 1이며, 오류에 파일 본문·인증정보가 있는 URL·DB 예외 원문을 출력하지 않는다.
   파일은 수정하지 않는다. `[unchanged]`와 `revision=없음`은 이미 승인된 동일 자료일 수 있다.

### 관리자 승인과 AI 담당자 인계

Admin **공식자료 변경 후보**에 `수동 TXT`로 표시된다. 상세의 이전/새 본문, `metadata`의 공식
출처·이용조건·원본 파일 지문·등록자와 안내문을 확인한다. 관리자가 공식 원문을 직접 대조하고
가공 허용·적용 지역·발행일·최신성을 판단한 뒤 기존 **선택한 변경 후보 승인**을 사용한다.
승인 동작은 원문 대조와 조건 검토를 마쳤다는 관리자 판단이며 URL 검증의 자동 판정이 아니다.
검토 결과·로그인 관리자·검토일은 기존 승인 필드와 Admin 로그에 남는다. 메타데이터는 읽기 전용이다.
조건을 수정하려면 TXT를 다시 등록한다. 기존 수동 자료를 자동 재수집해 조건 메타데이터를
누락한 후보도 승인할 수 없다. 조건 확인 정보를 갖춘 TXT로 다시 등록해야 한다.

- 신규 승인: 본문/해시 반영 → `PENDING / APPROVED / ai_reprocessing_required=True`.
- 반려: 신규 자료는 검색에서 제외한다. 변경 후보 반려는 기존 승인 자료를 보존한다.
  반려 이력은 유지하며 재등록 시 새 검토 후보가 생길 수 있다.
- 승인 전에는 기존 ACTIVE 자료만 계속 사용한다. 새 본문 승인 후에는 재처리 완료까지 검색에서 제외한다.
  Admin에서도 미승인 TXT·미확인 조건·재처리 대기 자료를 ACTIVE로 강제 저장할 수 없다.
- AI 담당자는 승인된 문서 ID, 공식 URL·조건, `content_hash`, `version`, 재처리 플래그를 전달받는다.
  기존 `from ai.chatbot import build_source_chunks`의 `build_source_chunks([문서ID])`로 명시적으로
  처리한다. 이 등록 명령은 AI를 호출하지 않는다. 실제 임베딩 환경·자격증명 구성은 AI 담당 범위다.
- 기존 AI 완료 검증이 성공하면 `ACTIVE / APPROVED / ai_reprocessing_required=False`가 된다.
  INACTIVE, 미승인, 실패, 처리 도중 해시/승인 버전 변경은 자동 활성화하지 않는다.
  이 기능은 AI 청크·임베딩·RAG 알고리즘을 변경하지 않는다.

기존 AI의 **비정상 임베딩 응답 검증은 미해결 [#78](https://github.com/cha771133-sys/ChungnamOn/issues/78)**
범위다. Ollama 본문 임베딩이 빈 배열을 반환하면 청크 없이 완료·ACTIVE로 기록되는 사례가
별도 mock으로 재현되어 있다. 이번 TXT 기능의 정상 응답·예외 테스트가 이 문제를 해결한 것은
아니며, 실제 임베딩 운영 전 AI 담당자가 #78의 응답 검증과 완료 처리를 확인해야 한다.

신규 모델 필드·마이그레이션은 없다. 테스트는 Django 격리 DB와 임시 TXT, 로컬 hash 임베딩 또는
mock만 사용한다. Railway·운영 DB·실제 공식 사이트·외부 임베딩/LLM 서비스로 검증하지 않는다.

```powershell
$env:DATABASE_URL = "sqlite:///:memory:"
python manage.py test sources.tests.test_manual_import sources.tests.test_approval_indexing
```

## 구현 범위와 실행 상태

공공데이터포털의 대한민국 공공서비스(혜택) 정보와 법제처 자치법규를 기존 공식자료 저장소에
연결한다. HTML 수집은 P1-7의 고정 URL 목록·Collector·본문 파서를 재사용한다.
수집 성공은 자료의 법적 효력이나 외국인 개인에게 적용되는 지원 자격을 보증하지 않는다.

**정기 자동 실행 트리거는 연결하지 않았다.** 기존 프로젝트에는 재사용 가능한 주기 실행기가
없다. 알림 명령도 호출형이며 일정 의존성으로 차단되어 있다. 이 작업은 재확인 시점 계산과
한 번씩 호출 가능한 명령을 제공한다. 웹 서버 시작 시 스레드나 스케줄러를 실행하지 않는다.
Railway 설정·환경변수·Cron·시작/빌드 명령·배포 파일·운영 DB는 변경하지 않는다.

Notion의 전체 일정·백엔드 하네스·F6-3 원문은 문서 URL과 조회 도구가 제공되지 않아 확인하지
못했다. 사용자 제공 확정 F6-3와 저장소 AGENTS.md·CONTRIBUTING.md를 적용했다.
확인하지 못한 마감일이나 운영 정책을 가정하지 않는다.

## 명세와 실제 확인 범위

- [공공데이터포털 공식 안내](https://www.data.go.kr/data/15113968/openapi.do)에서 연결한
  [활용명세](https://infuser.odcloud.kr/api/stages/44436/api-docs?1684891964110)의
  `/api/gov24/v3/serviceList`, `serviceDetail`, `supportConditions` 및 응답 정의를 확인했다.
  인증은 `Authorization: Infuser …` 헤더, 페이징은 `page`·`perPage`, ID는
  `cond[서비스ID::EQ]`, 목록명 검색은 `cond[서비스명::LIKE]`다.
- [법제처 목록 명세](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=ordinListGuide)와
  [본문 명세](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=ordinInfoGuide)를 확인했다.
  HTTPS `www.law.go.kr/DRF/lawSearch.do`·`lawService.do`, `target=ordin`, `type=XML`,
  `OC`를 사용한다. 목록은 기본 `nw=1`이며 클라이언트는 `nw=2` 연혁 조회도 분리 제공한다.
  본문은 안정적인 `자치법규ID`를 `ID`로 요청한다. 공포일과 시행일을 별도로 보존한다.
- 법제처 HTTPS 접근과 **HTTP 200 오류 본문**을 실제 확인했다. 인증값 누락은 JSON의
  `result`·`msg`, 공식 가이드의 공개 샘플은 XML의 `Response/result/msg`로 서버 등록 오류를
  반환했다. 이들은 성공으로 처리하지 않는다.
- 실제 개인 인증정보는 없다. **인증된 목록·상세·지원조건 수집 성공은 미검증**이다.
  자동 테스트 fixture는 명세를 바탕으로 만든 가상 자료이며 실제 정책·조례가 아니다.

## 등록과 호출

API 사용 시 로컬 실행 환경에서만 `PUBLIC_DATA_SERVICE_KEY`, `LAW_API_OC`를 설정한다.
소스·명령 인자·Git에 키를 넣지 않는다. 법제처는 신청 계정의 호출 서버 등록 조건도 충족해야
한다. 명령은 Railway 환경변수나 배포 설정을 구성하지 않는다.

신규 API 검색은 제공자 하나와 좁은 검색어를 명시한다. 지원정책의 신청 자격은 원문과
조건 코드 그대로 보존하고 요약하지 않는다. 먼저 저장 없는 결과를 확인할 수 있다.

```powershell
python manage.py refresh_official_sources --provider public_data --discover --query 외국인 --region 아산시 --dry-run --limit 10
python manage.py refresh_official_sources --provider law --discover --query 지방세 --region 충청남도 --limit 10
python manage.py refresh_official_sources --provider all --due-only --limit 20
python manage.py refresh_official_sources --provider html --dry-run --limit 3
```

기본 실행 상한은 20건, 설정 가능한 상한은 1,000건이다. API 페이지는 100건 이하,
검색은 최대 10페이지다. 목록에 더 많은 자료가 남으면 검색 범위를 좁히도록 오류를 남긴다.
정부24 명세에 지역/기관 검색 파라미터가 없으므로 제한된 목록에서 소관기관명과 기관 유형을
필터링한다. `--agency`로 기관명을 더 좁힐 수 있다. 전국 범위는 중앙행정기관 자료만 선택한다.
법제처는 충남 `org=6440000`, 천안 `sborg=4490000`, 아산 `sborg=4520000`을 사용하고
응답 관할기관을 재검사한다. 발행기관·수집 범위와 실제 적용 지역을 같다고 단정하지 않으며
API 자료의 `region`은 `확인 필요`, 관할·수집 지역은 메타데이터에 별도로 보관한다.

일반 실행은 **등록된 대상만** 재수집한다. HTML 대상은 기존 YAML을 재사용하며 YAML에서
비활성인 자료와 Admin에서 중지한 대상은 제외한다. 삭제·비활성화로 기존 자료를 삭제하지
않는다. API 대상은 최초 검색 또는 권한 있는 관리자의 수집 대상 등록으로 추가한다.
법제처 대상의 등록 메타데이터에는 `{"collection_region": "아산시"}`처럼 범위를 지정한다.
HTML 수동 대상은 `title`·`agency`·`region`·`category`와 확인된 경우만 `published_at`이 필요하다.

`--dry-run`도 **실제 외부 요청**과 파싱·비교를 수행하지만 대상 등록·CrawlLog·자료·검토·AI
상태를 쓰지 않는다. 신규/후보 수는 저장 시 예상 결과다. 실패가 있으면 계속 가능한 다른
제공자를 처리한 후 부분/전체 실패를 구분하여 종료 코드 1을 반환한다. 정상·빈 대상은 0이다.

## 저장과 검토

- `SourceDocument`의 PK·본문·해시·출처를 재사용한다. `(provider, external_id)` 조건부
  UNIQUE로 API 자료를 식별하고 기존 HTML의 빈 외부 ID는 허용한다.
- 공식 상세링크가 없거나 인증 요청 URL 등 공개 근거로 부적합하면 API 자료의 `source_url`은 NULL이다. 가짜 URL이나 인증 요청 URL을
  만들지 않는다. 링크 없음은 메타데이터에 표시하며 AI 근거 제공 대상에서 제외한다.
  법제처는 매번 현행 목록의 ID·일련번호를 본문과 비교하여 같은 개정 버전의 제공 링크만
  사용한다. 부적합 링크는 저장하지 않고 외부 ID로 수집과 나머지 목록 처리를 계속한다.
  현행 목록 확인 불가는 별도 표시하며 폐지·실효로 자동 단정하지 않는다.
- 최초 본문은 `SourceRevision`에 검토 대기로 저장한다. 기존 자료의 변경도 승인 본문을
  덮어쓰지 않고 이전 원문·해시·메타데이터를 후보에 함께 보관한다.
- `last_success_at`은 마지막 성공 수집 시각이다. `collected_at`은 **현재 승인된 본문**을
  실제 확인한 시각이며, 승인 시 후보의 실제 수집일을 사용한다. 관리자 검토일은 별도다.
  후보만 확인한 시각을 이전 승인 본문 확인일로 바꾸지 않는다.
- 동일 승인 본문은 확인일만 갱신하고 새 후보·AI 재처리 요청을 만들지 않는다. 검토 중인 동일
  후보도 중복 생성하지 않는다. 실패는 기존 정상 자료·해시·두 성공 확인일을 바꾸지 않는다.
- Django Admin의 **공식자료 변경 후보** 목록에서 이전/새 본문을 확인한 뒤 승인 또는 반려
  액션을 사용한다. 운영 staff와 두 모델 수정 권한이 필요하며 실제 로그인 관리자·시각과
  Admin 이력을 남긴다. 자동 수집을 수동 검토로 기록하지 않는다.
- 승인은 현재 해시·승인 정보 버전·최신 관측 해시·성공 확인일을 다시 비교하여 원자적으로 반영한다.
  같은 본문의 출처·날짜가 이전 값으로 돌아오더라도 이전 관측의 후보는 승인할 수 없다.
  더 최신 후보가 있거나 기존 관리자 정보가 수정되면 오래된 승인은 충돌로 거부한다.
  반려는 승인 본문을 보존하고 이후 재수집을 허용한다.

Admin에서 자료별 최신성·검토·실패 유형·외부 ID·기존 정상 본문 존재 여부를 조회한다.
마지막 본문 확인일 순 정렬 및 유형·STALE·검토 필요 필터를 제공한다. 수집 대상 목록에서
활성 여부·주기·다음 확인 예정 시각을 관리한다. Admin 요청 안에서 외부 수집은 실행하지 않는다.

## 주기와 실행 보호

운영 정책 미확정으로 **법규 7일 / 지원정책 3일 / HTML 14일**을 구현 기본값으로 둔다.
대상별 `interval_hours`를 Admin에서 바꿀 수 있다. Django 설정
`OFFICIAL_SOURCE_INTERVAL_HOURS` 매핑으로 유형 기본값도 덮어쓸 수 있지만 이 작업은 공통
설정 파일이나 운영 설정을 변경하지 않는다. `next_due_at()`은 성공 확인일+주기를 계산한다.
실패 시 재시도 간격은 1·2·4·8·16·24시간이며 이후 24시간이다. 무제한 즉시 재시도하지 않는다.

`CURRENT`·`STALE`·`REFRESH_FAILED`·`REVIEW_REQUIRED`는 계산 상태다. 검토 필요를 우선
표시하며 실패 여부·발행일·시행일은 별도로도 제공한다. 미래 발행/시행일도 검토 대상으로
표시한다. 오래된 확인일·미래 시행일만으로 정책의 유효/폐지를 판단하지 않는다.

대상별 조건부 DB UPDATE로 30분 실행 소유권을 얻고 API 호출 전 RUNNING 로그를 기록한다.
네트워크 동안 DB 잠금을 유지하지 않는다. 저장할 때 소유권과 시작 순서를 다시 확인하고
자료·후보·성공 로그를 함께 확정한다. 중복 실행은 요청 없이 건너뛰며, 비정상 종료는 잠금
만료 후 재실행할 수 있다. 인수 시 중단된 RUNNING 로그도 실패로 마감한다. 소유권을 잃은
이전 실행은 새 소유자의 자료·잠금·재시도 상태를 덮어쓰지 않는다. SQLite 쓰기 경합은 안전한
실패가 될 수 있으므로 재시도한다. PostgreSQL 실제 동시 실행은 별도 테스트로 검증한다.

통신은 P1-7의 공인 DNS 검증·IP 고정·원 호스트 TLS 검증·1초 간격·5/15초 타임아웃·본문
30초 제한·2MiB 상한을 재사용한다. 429/일부 5xx/연결 오류는 최초 외 최대 2회 재시도하며
Retry-After가 60초를 넘으면 종료한다. API 리다이렉트는 키 전달을 막기 위해 전부 거부한다.
XML 파서에서 DTD·엔티티 선언을 거부한다. 응답 전문·외부 예외 원문을 로그로 복사하지 않는다.

## AI 경계와 기존 명령 호환

청크·임베딩 생성과 검색은 AI 모듈이 담당한다. 최초·본문 변경 승인 시 `ai_reprocessing_required=True`로
기록하고, 기존 청크와 임베딩은 보존한다. `SourceChunk.source_content_hash`는 AI 작업이 읽은
원문 해시 저장 공간이며 이 값이 비어 있는 기존 청크는 최신성이 확인되지 않은 상태다.
`is_current`와 문서 `ai_eligible`은 해시·검토·사용 상태·재처리·공식 링크를 함께 확인한다.

[AI PR #36](https://github.com/cha771133-sys/ChungnamOn/pull/36)의 청크 생성·검색 연동에 이어
[AI PR #64](https://github.com/cha771133-sys/ChungnamOn/pull/64)에서 재처리 완료 시 자동 `ACTIVE` 전환이 구현되었다.
`build_source_chunks()`는 관리자 승인·공식 링크·본문·해시가 있고 `INACTIVE`가 아닌 자료를
처리하며, 승인 후 재처리 대기 중인 `PENDING` 자료도 포함한다. 검색은 `ACTIVE`·관리자 승인·
재처리 완료·공식 링크 조건을 충족하는 자료의 현재 승인 본문 해시와 일치하는 청크만 사용한다.
승인된 새 본문이 이전 청크와 섞이지 않도록 재처리가 필요한 승인 후 자료는 `PENDING`으로
두고 `INACTIVE`는 유지한다. 같은 본문의 출처 등 메타데이터만 검토한 경우에는 완료된
재처리를 다시 요청하지 않는다. 과거 청크를 삭제하거나 ID를 재사용해서 완료 처리하면 안 된다.

**자동 상태 전환과 AI 재처리 실행은 별개다.** `review_revision()`과 Admin 승인 액션은
재처리 필요 상태만 저장하며, `build_source_chunks()`를 호출하거나 작업 큐에 등록하지 않는다.
현재 승인 후 재처리는 아래 명령으로 **수동 실행**한다. 자동 `ACTIVE` 전환은 이 함수가 실행되어
정상 완료 조건을 만족할 때 수행되며, 승인만으로 재처리가 자동 실행되지는 않는다.

```powershell
python manage.py shell -c "from ai.chatbot import build_source_chunks; print(build_source_chunks())"
```

수집·검토·AI 처리 단계별 상태와 변경 필드는 다음과 같다.

| 단계 | 상태 및 변경 필드 |
| --- | --- |
| 신규 공식자료 수집 | 문서는 `status=PENDING`, `review_status=PENDING_REVIEW`, `version=0`, `ai_reprocessing_required=False`이며 승인 본문·해시는 비어 있다. 수집 본문·해시는 검토 대기 `SourceRevision`에 저장한다. 문서의 `last_success_at`·`observed_started_at`·`observed_hash`·`updated_at`을 기록하며 승인 본문 확인일 `collected_at`은 아직 없다. |
| 관리자 승인·AI 재처리 대기 | 후보의 본문·해시·출처 메타데이터와 `collected_at`을 문서에 반영한다. 문서와 후보의 `review_status=APPROVED`·`reviewed_by`·`reviewed_at`을 기록하고, 문서의 `version`을 1 증가시키며 `updated_at`을 갱신한다. 최초·본문 변경 승인 시 `ai_reprocessing_required=True`, `status=PENDING`으로 두되 기존 `INACTIVE`는 유지한다. |
| AI 재처리 성공 | 아래 완료 검증을 통과한 재처리 대기 자료만 `status=ACTIVE`, `ai_reprocessing_required=False`, `version+1`, `updated_at`을 함께 반영한다. 청크는 승인 본문 해시를 기록하여 추가하고 기존 청크·ID는 보존한다. |
| AI 재처리 실패·청크 없음 | 임베딩 예외가 나면 해당 자료의 저장·완료 처리를 하지 않고 예외를 반환한다. 분할 결과가 비어 있어도 완료로 기록하지 않는다. 문서의 기존 상태·버전·재처리 플래그와 기존 청크는 유지하며 별도 실패 상태로 바꾸지 않는다. 앞서 완료된 다른 자료까지 되돌리지는 않는다. |
| 관리자의 사용 중지 | Admin에서 `status=INACTIVE`로 저장하면 `version+1`·`updated_at`을 반영하며 승인 정보와 재처리 플래그는 유지한다. 수집·재승인·AI 완료가 자동으로 재활성화하지 않으며 검색에서도 제외한다. |
| 본문 변경 후 재승인 | 변경 수집만으로 승인 본문·해시·청크는 바꾸지 않는다. 변경 후보 승인 시 새 본문·해시와 검토 정보를 반영하고 `version+1`, `ai_reprocessing_required=True`, `status=PENDING`(기존 `INACTIVE` 유지)으로 전환한다. 이전 청크는 남지만 재처리 플래그·상태·해시 조건으로 최신 검색에서 제외된다. |

같은 본문의 메타데이터만 승인하고 이미 재처리가 끝났다면 플래그를 다시 켜지 않는다.
단, 공식 링크가 없으면 `PENDING`으로 두며 AI 처리 대상에서도 제외한다. 같은 후보의 중복
승인은 `REVIEW_CONFLICT`로 거부하여 버전·상태를 다시 변경하지 않는다.

**구현된 자동 `ACTIVE` 전환 조건:** `indexing.py`의 `build_source_chunks()`는 자료별 처리
시작 시점의 본문 해시와 승인 정보 버전(`version`)을 보관한다. 완료 직전에는
`_lock_if_unchanged()`가 청크 저장·상태 갱신과 같은 트랜잭션에서 최신 자료 행을
잠그고, **완료 처리 자체가 `version`을 증가시키기 전에** 시작 시점의 해시·버전과 비교한다.
이때 `review_status=APPROVED`와 현재 `status != INACTIVE`도 다시 확인한다.
새 청크·임베딩을 저장하거나 현재 본문 해시와 설정된 임베딩 차원의 청크가 이미 있음을
확인한 뒤, `_finish()`·`_complete_and_activate()`가 `ai_reprocessing_required=True`인 경우에만
재처리 완료와 자동 `ACTIVE` 전환을 함께 반영한다. 조건부 UPDATE에도 해시·버전·승인·
`INACTIVE` 제외·재처리 필요 조건을 다시 적용한다. 최신 청크가 있어 청크 생성을 건너뛰고
완료만 기록하는 경로에도 같은 검증을 적용한다.

완료 처리가 문서에서 변경하는 필드는 **`status`·`ai_reprocessing_required`·`version`·`updated_at`뿐**이다.
본문·해시·제목·기관·승인자·승인일·수집 확인일은 변경하지 않는다. 이미 플래그가 `False`인
자료는 완료로 다시 기록하거나 상태·버전을 바꾸지 않는다. 이미 `ACTIVE`지만 재처리 대기라면
완료와 `version+1`만 반영하며 새 활성화 수(`activated`)에는 포함하지 않는다.

단, 새 청크 경로는 임베딩 함수가 예외 없이 반환하면 성공으로 간주하며, `indexing.py`는
반환된 벡터 개수·차원을 직접 검증하지 않는다. Gemini 임베더는 이를 검증하지만 Ollama
임베더에는 같은 검사가 없다. 비정상 Ollama 응답(`embeddings=[]`)을 모의하면 청크가 없어도
완료·`ACTIVE`로 기록되는 문제가 재현되었다. 이는 AI 담당 범위의 미해결 결함이며,
실제 외부 서비스에서의 발생 여부는 미검증이다.

완료 직전 해시·버전이 일치하지 않거나 승인이 취소·반려되었거나 `INACTIVE`라면 해당 작업의 재처리 완료 기록과 자동 `ACTIVE`
전환을 수행하지 않는다. 이전 작업은 최신 자료의 상태·재처리 플래그·완료 기록을 덮어쓰지 않는다.
최신 본문의 재처리가 아직 완료되지 않았다면 `ai_reprocessing_required=True`를 유지한다.
최신 본문의 재처리가 이미 완료되어 플래그가 `False`라면 그 값과 최신 작업의 완료 기록을 보존한다.
해당 작업에서 계산한 청크는 DB에 저장하지 않는다.

- 관리자가 승인한 공식자료는 AI 재처리가 정상적으로 완료되면 자동으로 `ACTIVE` 상태로 전환한다.
- 관리자가 사용 중지한 `INACTIVE` 자료는 자동 전환하지 않는다. 시작 시점에 제외했더라도
  완료 직전에 다시 확인하여, AI 처리 도중 사용 중지된 자료도 `INACTIVE` 상태를 유지한다.
- AI 처리 시작 시점과 완료 직전의 본문 해시 또는 승인 정보 버전(`version`)이 일치하지
  않는 자료는 자동 전환하지 않는다.
- 관리자 승인을 받지 않은 자료는 자동 전환하지 않는다.
- AI 재처리에 실패한 자료는 기존 상태를 유지한다.

Admin 폼도 저장 트랜잭션에서 상태와 무관하게 행을 잠그고 화면을 연 시점의 숨김 버전을
검사한다. 그 뒤 다른 승인·관리 수정이 있었다면 제목·기관·분류 등을 이전 값으로 되돌리지
않고 최신 화면을 다시 열도록 안내한다. 최신 화면에서도 재처리 대기 자료를 ACTIVE로
전환하는 요청은 검증 오류로 거부하며, 정상적인 사용 중지 전환은 허용한다.

기존 `collect_sources`는 등록하지 않은 기존 대상에 대해 이전 동작을 유지한다. 새 재수집
관리 대상으로 등록된 HTML은 이전 명령으로 실행해도 검토 저장 서비스를 거치므로 승인
자료를 우회하여 덮어쓰지 않는다. 등록 전에 이미 시작된 이전 수집기도 저장 시 관리 전환을
다시 검사하며, 관리 대상으로 바뀌었다면 저장을 거부하고 새 명령으로 재실행하도록 기록한다.
기존 원문·청크·CrawlLog 데이터는 마이그레이션으로 보존한다.

## 검증

단위 테스트는 네트워크 mock과 명세 기반 fixture만 사용한다. 신규·동일·변경·실패, 승인·반려·
오래된 승인 충돌, 유형 주기·재시도·만료 잠금 복구·중복 실행, 명령의 dry-run·상한·부분 실패,
관리자 권한과 마이그레이션 보존을 검사한다. PostgreSQL 전용 실제 경합 테스트는 SQLite에서
건너뛰며 기존 PostgreSQL 16 GitHub CI에서 실행한다. 운영 DB에서 검증하지 않는다.

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```
