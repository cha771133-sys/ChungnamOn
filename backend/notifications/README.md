# P3-14 알림 기반 및 연동 계약

**부분 구현이다. 자동 마감 알림 서비스는 아직 동작하지 않는다.** 기준 `dev`의 `007f4ba`에서
`documents.Schedule`이 없으며, GitHub 전체 이슈·PR과 원격 브랜치에도 P2-11 구현이 없었다.
Schedule을 새로 설계하거나 A 담당 코드를 가져오지 않았다. 추적: [#32](https://github.com/cha771133-sys/ChungnamOn/issues/32).

| 구현됨 | 선행 작업 때문에 미구현 |
| --- | --- |
| 기본 비활성 수신 설정, 재활성화 시각 | 실제 Schedule 소유권·완료·마감일 기반 대상 조회 |
| 다중 기기 등록·갱신·해제·계정 이전 | Schedule FK의 DeadlineReminder / ReminderDelivery |
| Firebase Admin 발송 Adapter·안전한 오류 분류 | 이벤트·기기별 발송 이력, 작업 점유, 재실행 중복 방지 |
| 한국 날짜 D-7/D-1 계산·당일 지연 정책 순수 함수 | 일정 수정·삭제 연동, 실제 알림 상태 조회 API |
| JWT API, Swagger, 조회 전용 관리자, mock 테스트 | 정상 발송 명령, 집계·limit·dry-run 실제 대상 조회 |

명령 `python manage.py send_deadline_reminders`는 현재 `SCHEDULE_INTEGRATION_UNAVAILABLE` 오류와
종료 코드 1을 반환한다. `--dry-run`과 `--limit`도 동일하다. Firebase 호출·DB 변경·가짜 0건 성공은 없다.
실제 발송 대상이 없는 경우와 아직 대상 조회를 구현하지 못한 경우를 혼동하지 않는다.
`GET /api/notifications/reminders/`는 제공하지 않는다. 알림 설정 활성화는 현재 자동 발송을 뜻하지 않는다.

## 인증 및 요청·응답

모든 API는 `Authorization: Bearer <기존 JWT>`가 필요하다. 기존 공통 `{code, message, details}` 오류를 사용한다.
400은 입력 오류, 401은 인증 오류, 404는 본인 기기 없음, 503은 저장소 오류다.
클라이언트 사용자 ID와 추가 필드는 거부한다. Swagger: `/api/docs/`, 스키마: `/api/schema/`.

### 기기 등록·갱신

`POST /api/notifications/devices/`

```json
{
  "registrationId": "<FCM에 등록된 22자리 FID>",
  "registrationType": "FID",
  "platform": "WEB"
}
```

위 식별자는 설명용이며 실제 요청에는 Firebase SDK가 발급·등록한 FID를 사용한다.
선택 필드 `replacesDeviceId`는 FID가 바뀌었을 때 기존 **본인** 기기 ID다.
새 기기 등록과 이전 등록 비활성화를 하나의 트랜잭션으로 수행한다.
지원 플랫폼은 `WEB`, `ANDROID`, `IOS`다. 신규·반복 등록 모두 200을 반환한다.

```json
{
  "id": 1,
  "registrationType": "FID",
  "platform": "WEB",
  "isActive": true,
  "createdAt": "2026-11-05T09:00:00+09:00",
  "updatedAt": "2026-11-05T09:00:00+09:00"
}
```

FID 원문·해시·소유자 정보는 응답에 없다. 동일 FID의 SHA-256 비교 인덱스가 DB에서 전역 고유하다.
다른 계정이 같은 FID를 등록하면 현재 JWT 사용자로 해당 행을 이전하고 최근 접수 시각을 초기화한다.
이전 계정은 해당 기기를 해제하거나 활성 기기 수로 조회할 수 없다. 이전 등록 세대의 늦은 발송 결과도
새 계정/재등록된 기기를 비활성화하거나 정상 접수로 변경하지 못하도록 조건부 갱신한다.
등록은 수신 동의를 자동 활성화하지 않는다. 서버가 실제 브라우저 권한·FID 소지를 증명하는 API는 아니다.

`DELETE /api/notifications/devices/{id}/`는 본인 기기를 비활성화하며 204를 반환한다.
같은 소유자의 반복 해제도 204, 다른 사용자의 ID는 404다. JWT 로그아웃 자체는 이 API를 대신 호출하지 않는다.

### 수신 설정

`GET /api/notifications/preferences/`

```json
{"enabled": false, "registeredDeviceCount": 0}
```

미설정 상태는 비활성이다. GET은 설정 행을 생성하지 않는다. 기기 수는 현재 계정의 활성 기기만 센다.
`PATCH /api/notifications/preferences/`에 `{"enabled": true}` 또는 `{"enabled": false}`를 보낸다.
문자열 `"true"`, 숫자 `1`, null은 거부한다. 응답은 GET과 같다.
최초 활성화/재활성화 시 `enabled_since`를 기록하고, 비활성화 시 null로 지운다.
이미 활성화된 상태의 반복 true는 동의 시작 시각을 변경하지 않는다. `updated_at`은 요청마다 갱신한다.
활성 기기 수와 서비스 동의는 브라우저 Notification Permission 및 실제 수신 보장이 아니다.

## Firebase 및 프론트 계약

2026-11-05 공식 문서와 설치한 `firebase-admin==7.7.0`의 `Message(fid=...)` 지원을 확인했다.
현재 프론트에는 Firebase 의존성/기기 토큰 계약이 없으므로 **FID만 채택**했다.
TOKEN/FID를 혼용하지 않으며 `registrationType=TOKEN`은 400이다.
프론트는 `register`/`onRegistered`를 지원하는 Firebase Web SDK를 사용해야 한다.
`register()`는 `Promise<void>`이며 FID는 `onRegistered` 콜백에서 얻는다. `getId()`만 호출한 FID로는
FCM 푸시 등록을 대신할 수 없다. 서버는 형식만 확인하며 실제 FCM 등록 유효성은 발송 응답에서 확인한다.

프론트 담당자가 구현할 순서:

1. 동일 Firebase 프로젝트·HTTPS·지원 브라우저와 VAPID 공개키를 확인한다.
2. 사용자 의사에 따라 브라우저 알림 권한을 요청하고 Firebase 호환 Service Worker를 등록한다.
3. `onRegistered` 콜백을 연결하고 `register(messaging, {vapidKey, serviceWorkerRegistration})`로 등록한다.
4. 콜백 FID를 JWT로 위 등록 API에 전달하고 응답 기기 ID를 계정별로 보관한다.
5. FID 변경 시 `replacesDeviceId`를 전달한다. 계정 전환 시 재등록한다.
6. 서비스 수신 동의를 별도로 PATCH한다. 로그아웃 전 기존 JWT로 기기를 DELETE하고 로컬 ID를 비운다.
7. 향후 수신 메시지의 `data.scheduleId`로 JWT와 기존 소유권 검증을 거쳐 상세를 조회한다.

현재 `frontend/public/sw.js`는 일반 Web Push 예시이며 Firebase 호환 수신을 보장하지 않는다.
기존 프론트 진입 형식 `/?schedule=ID`에 맞춰 Adapter는 같은 출처의 상대 경로만 만든다.
외부 클릭 URL을 입력으로 받지 않는다. 현재 일정 화면은 모의 ID를 사용하므로 실제 DB 일정 라우팅도 후속 연동이 필요하다.
기존 프론트 파일·SDK·Service Worker는 이번 작업에서 변경하지 않았다.

Adapter는 일반 업무명인 “등록한 행정업무”, 남은 일수, 마감 날짜만 표시한다.
원문 업무명·문서·해석·금액·신상정보는 전달받거나 본문에 넣지 않는다.
`data`는 `type=DEADLINE_REMINDER`, `scheduleId`, 선택 `documentId`, `daysBefore`, `deadlineDate`, 상대 `url`이다.
향후 호출자는 반드시 DB에서 확인한 식별자를 전달해야 한다. 실제 수신·읽음·업무 완료·법적 기한 확정을 의미하지 않는다.

### 운영 자격증명과 결과

앱 시작·API·마이그레이션은 Firebase 인증을 요구하지 않는다. 실제 Adapter 호출 시 전용 named app을 지연 초기화한다.
Railway 비밀 환경변수 `FIREBASE_SERVICE_ACCOUNT_JSON`에 서비스 계정 JSON 전체를 주입하거나,
안전하게 마운트한 파일 경로를 `GOOGLE_APPLICATION_CREDENTIALS`로 제공하여 ADC를 사용한다.
둘 다 설정하면 JSON 방식이 우선이다. 비밀값은 저장소·PR·로그에 출력하지 않는다.
서비스 계정은 해당 Firebase 프로젝트의 FCM 전송 권한이 필요하다. 요청 타임아웃은 SDK 옵션으로 10초다.

| Adapter 상태 | 의미·기기 처리 |
| --- | --- |
| ACCEPTED | FCM 접수. provider_message_id 반환. 수신/읽음 아님 |
| INVALID_DEVICE | UNREGISTERED. 현재 등록 세대만 비활성화 가능 |
| CONFIGURATION_ERROR | 자격증명·프로젝트·VAPID/APNs 설정 오류. 기기 유지, 향후 작업자는 실행 중단 필요 |
| RETRYABLE | 쿼터 거부. 추후 제한된 재시도 정책 적용 가능. 현재 재시도 없음 |
| PERMANENT_ERROR | 잘못된 페이로드/인자. 기기를 임의로 무효화하지 않음 |
| UNKNOWN | 네트워크·서비스 장애 등 결과 불확실. 자동 재전송 금지 |

예외 원문은 저장/출력하지 않고 고정 오류 코드만 반환한다.
Adapter는 DB 이력을 만들지 않는다. `record_device_result`는 현재 등록 세대의 마지막 정상 접수/무효화만 반영하는 내부 함수다.
일반 사용자 발송 API나 웹 요청 발송 경로는 없다. 앱 자체 재시도는 없지만 SDK 내부 재시도가 있으므로
한 번의 함수 호출도 외부 정확히 한 번 발송을 보장하지 않는다.
FID 원문은 전송에 필요해 DB에 저장한다. 해시는 암호화가 아니다. 원문은 일반 로그/API/관리자 화면에 표시하지 않는다.
관리자는 활성 superuser의 조회만 허용하며 추가·수정·삭제·작업은 막는다. 운영 DB 접근·백업 권한과 암호화는 별도 관리한다.

## Railway Cron 준비

**현재 명령은 차단되므로 Cron을 활성화하지 않는다.** 다음은 P2-11 및 실제 발송 이력·점유 구현 후 적용할 설정이다.

1. 같은 저장소에서 별도 Cron Service를 만들고 Root Directory를 `backend`로 설정한다.
2. 웹 서비스와 같은 PostgreSQL `DATABASE_URL`을 참조하고 `DJANGO_SECRET_KEY`, Firebase 자격증명을 별도 서비스에도 제공한다.
3. 새 마이그레이션은 배포 절차에서 한 번 적용한다. 웹 Gunicorn 시작 명령/배포 설정은 변경하지 않는다.
4. Cron Service 시작 명령을 `python manage.py send_deadline_reminders`로 설정한다.
5. Cron Schedule `0 0 * * *`는 UTC 00:00, 한국시간 09:00이다.
6. 우선 dry-run으로 대상·변경 없음, PostgreSQL 연결과 설정을 점검한 뒤 실제 수신 테스트를 별도로 수행한다.
7. 실행 후 프로세스/DB 연결이 종료되고 이전 실행이 Active로 남지 않는지 확인한다.

Railway는 UTC 기준이며 정확한 분 단위 실행을 보장하지 않는다. 이전 실행이 끝나지 않으면 다음 실행을 건너뛴다.
단발 명령을 유지하며 앱 시작/Gunicorn 프로세스에서 스케줄러를 시작하지 않는다.
Railway 프로젝트에 접근하지 않았고 실제 배포·Firebase 기기 수신은 검증하지 않았다.

## P2-11 이후 남은 구현

A 담당과 Schedule의 실제 PK·소유권·업무명·마감일 타입·완료 상태·생성/수정 시각·문서·삭제 정책을 먼저 합의한다.
그 후에만 정상 FK 마이그레이션을 추가한다. 현재 마이그레이션은 사용자 모델에만 의존하며 존재하지 않는 모델 참조가 없다.

실제 일정 통합에서는 ORM으로 사용자/문서 소유권·미완료·유효 마감일·활성 동의/기기를 제한하고 배치 조회해야 한다.
동의 및 일정 등록이 예정 시각 이전이고 같은 한국 날짜의 지연 실행만 허용한다.
마감일 변경 전 미발송 이벤트는 취소하고 과거 이력은 보존한다. 기기가 없으면 성공으로 기록하지 않는다.

DB 고유 키 `(schedule, deadline_date, days_before)`와 `(reminder, device)`를 적용하고 짧은 트랜잭션에서
PROCESSING으로 원자적 점유한 뒤 외부 요청은 트랜잭션 밖에서 수행한다.
성공 기기는 재발송하지 않으며, SDK 접수 직후 기록 실패/작업 중 종료는 불확실 상태로 남겨 자동 회수·재발송하지 않는다.
이 정책과 다중 기기 PARTIAL/FAILED, 무효 기기 처리, 실패 종료 코드/집계를 실제로 구현·검증해야 한다.
SENT는 FCM 정상 접수만 뜻한다. 아직 이러한 이벤트 상태와 이력 API는 제공하지 않는다.

## 검증 및 출처

자동화 테스트는 실제 Firebase 서버나 인증에 접근하지 않는다. PostgreSQL 행 잠금 테스트는 SQLite에서 명시적으로 skip한다.
`python manage.py test notifications`로 API·기기 이전/교체·동의·DB 제약조건·시간 경계·Adapter 오류·명령 차단을 검증한다.
기존 Schedule/Checklist 회귀는 해당 모델이 아직 없어 실행할 수 없다.

- [Firebase 기기 대상 발송](https://firebase.google.com/docs/cloud-messaging/send/admin-sdk)
- [Web FID 등록·VAPID·Service Worker](https://firebase.google.com/docs/cloud-messaging/web/get-started)
- [Firebase Admin 인증](https://firebase.google.com/docs/admin/setup)
- [Firebase Admin 7.7.0 배포 정보](https://pypi.org/project/firebase-admin/7.7.0/)
- [Railway Cron의 UTC·실행 종료 조건](https://docs.railway.com/cron-jobs)
