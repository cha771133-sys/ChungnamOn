# P3-14 알림 기반 및 연동 계약

**Schedule 연동을 포함한 마감 알림 구현이다.** 기준 일정 계약은
[PR #47](https://github.com/cha771133-sys/ChungnamOn/pull/47)의 `documents.Schedule`이며, 이 모듈은
별도 일정 모델을 만들지 않는다. 연동 추적 이슈: [#50](https://github.com/cha771133-sys/ChungnamOn/issues/50).

| 구현됨 | 범위 밖 |
| --- | --- |
| 기본 비활성 수신 설정, 재활성화 시각, Schedule 소유권·완료·마감일 기반 대상 조회 | 일반 사용자 대상 발송 API·알림 이력 조회 API |
| 다중 기기 등록·갱신·해제·계정 이전 | Railway Cron 배포와 실제 기기 수신 확인 |
| Firebase Admin 발송 Adapter·안전한 오류 분류 | 일정별 사용자 설정(remind) |
| Schedule FK의 DeadlineReminder / 기기별 ReminderDelivery | FCM 수신/읽음 확인 |
| 한국 날짜 D-7/D-1 조회·당일 09:00 이후 지연 실행·중복 점유 |  |
| JWT API, Swagger, 조회 전용 관리자, mock 테스트 |  |

명령 `python manage.py send_deadline_reminders`는 한국 날짜 기준 D-7/D-1의 미완료 Schedule만 조회한다.
완료·당일·기한 경과·D-7/D-1 밖 일정은 대상이 아니다. `--dry-run`은 FCM 호출과 DB 변경 없이 대상 이벤트와
점유 가능한 기기 수만 출력하고, `--limit`은 처리할 일정 알림 이벤트 수를 제한한다. 실제 발송 대상이 없는 경우는
정상적으로 0건을 출력한다.
`GET /api/notifications/reminders/`는 제공하지 않는다. 알림 설정 활성화만으로 즉시 발송되지는 않으며,
Schedule·기기·예정 시각 조건을 함께 만족해야 한다.

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

Adapter의 잠금 화면 문구는 D-7 “등록한 일정의 마감일이 7일 남았습니다.”, D-1 “등록한 일정의 마감일이 내일입니다.”다.
원문 업무명·일정 제목·문서·해석·금액·신상정보는 전달받거나 본문에 넣지 않는다.
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
| CONFIGURATION_ERROR | 자격증명·프로젝트·VAPID/APNs 설정 오류. 기기 유지, 나머지 발송 중단 및 종료 코드 1. 설정 복구 후 같은 한국 날짜에 재실행 가능 |
| RETRYABLE | 쿼터 거부. 예정된 한국 날짜에 명령을 다시 실행하면 같은 기기 이벤트를 다시 점유할 수 있음 |
| PERMANENT_ERROR | 잘못된 페이로드/인자. 기기를 임의로 무효화하지 않음 |
| UNKNOWN | 네트워크·서비스 장애 등 결과 불확실. 자동 재전송 금지 |

예외 원문은 저장/출력하지 않고 고정 오류 코드만 반환한다.
Adapter는 DB 이력을 만들지 않는다. `record_device_result`는 현재 등록 세대의 마지막 정상 접수/무효화만 반영하는 내부 함수다.
일반 사용자 발송 API나 웹 요청 발송 경로는 없다. 앱 자체 재시도는 없지만 SDK 내부 재시도가 있으므로
한 번의 함수 호출도 외부 정확히 한 번 발송을 보장하지 않는다.
명령은 설정 오류 또는 무효 기기 외의 발송 실패/결과 불명이 있으면 집계를 출력한 뒤 종료 코드 1로 끝난다.
FID 원문은 전송에 필요해 DB에 저장한다. 해시는 암호화가 아니다. 원문은 일반 로그/API/관리자 화면에 표시하지 않는다.
관리자는 활성 superuser의 조회만 허용하며 추가·수정·삭제·작업은 막는다. 운영 DB 접근·백업 권한과 암호화는 별도 관리한다.

## Railway Cron 준비

**운영 Cron 활성화와 실제 기기 수신은 이 저장소 변경의 범위 밖이다.** 배포 전 다음 설정을 확인한다.

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

## Schedule 연동 정책

`notifications.0002_schedule_deadline_reminders`는 `documents.0003_schedule`과
`notifications.0001_initial`에 의존한다. 새 모델이므로 기존 PushDevice/NotificationPreference 데이터를 임의의
Schedule에 연결하는 데이터 마이그레이션은 없다. `DeadlineReminder(schedule, days_before)`와
`ReminderDelivery(reminder, device)`의 고유 제약으로 D-7/D-1 이벤트와 기기별 발송을 구분한다.

대상은 Schedule·Document 소유자 일치, 미완료, 활성 수신 동의, 활성 기기, 한국 날짜 D-7/D-1 조건을 모두 만족해야
한다. 일정/동의는 한국시간 09:00의 예정 시각 이전에 존재해야 하고 당일 지연 실행만 허용한다. 일정이 완료에서
미완료로 바뀌어도 성공 발송 이력은 초기화하지 않는다. Schedule·Document·User 삭제는 FK CASCADE로 이벤트와
이력을 함께 정리한다.
리마인더/발송 이력은 별도 관리자에 등록하지 않아 기존 문서·일정 관리자의 CASCADE 삭제 권한을 막지 않는다.

한 기기 전송은 짧은 트랜잭션에서 `PROCESSING`으로 점유한 뒤 FCM을 트랜잭션 밖에서 호출한다. `SENT`는 FCM 정상
접수일 뿐 수신/읽음이 아니며 같은 이벤트·기기에는 재발송하지 않는다. UNREGISTERED만 기기를 비활성화한다.
명시적으로 쿼터 거부된 `RETRYABLE`은 예정된 한국 날짜의 재실행에서만 시도할 수 있지만, 결과 불명(`UNKNOWN`)과
작업 중단의 `PROCESSING`은 중복 전송을 피하기 위해 자동 회수하지 않는다. FCM 접수와 DB 기록 사이에는 완전한
원자성이 없으므로 정확히 한 번 전송은 보장할 수 없다.

`RETRYABLE` 및 설정을 복구한 `CONFIGURATION_ERROR`는 **예정된 한국 날짜의 09:00 이후 재실행**에서만
다시 점유한다. 하루 한 번 Cron만 사용하면 다음 날에 지난 이벤트를 재시도하지 않는다.
외부 호출 직전 완료·삭제·수신 거부·기기 이전을 재확인하고, 발송 전 제외된 점유는 PENDING으로 돌린다.
재확인 직후 또는 FCM 호출 도중의 완료·삭제와 이미 접수된 푸시는 원자적으로 취소할 수 없다. 호출 도중 삭제된
Schedule의 이벤트/이력을 결과 저장 때 다시 만들지 않는다.

## #50 완료 체크리스트

- [x] 기존 기기 등록·중복·해제·다중 기기·JWT·Swagger 회귀 검증
- [x] Schedule FK와 `documents.0003_schedule` 의존성 및 기존 데이터 보존 마이그레이션 검증
- [x] D-7/D-1·한국 날짜 경계·완료/당일/경과 제외·완료 취소 재평가
- [x] 기기별 성공 중복 차단·독립 PostgreSQL 연결의 동시 명령 테스트 추가
- [x] 쿼터 재시도·설정 오류 중단/복구·불확실 결과 재전송 차단
- [x] Schedule·Document·User CASCADE와 완료/삭제/동의 변경/기기 이전 경합 검증
- [x] 실제 Schedule ID 문자열 payload 및 잠금 화면 민감 정보 비노출 검증
- [x] 전체 회귀·마이그레이션 일치·Swagger 검증 (실제 FCM 호출은 mock)

## 검증 및 출처

자동화 테스트는 실제 Firebase 서버나 인증에 접근하지 않는다. PostgreSQL 행 잠금 테스트는 SQLite에서 명시적으로 skip한다.
`python manage.py test notifications`로 API·기기 이전/교체·동의·DB 제약조건·시간 경계·Adapter 오류·Schedule 명령을 검증한다.
전체 `python manage.py test`로 기존 Schedule API와 JWT·Swagger를 함께 회귀 검증한다.

- [Firebase 기기 대상 발송](https://firebase.google.com/docs/cloud-messaging/send/admin-sdk)
- [Web FID 등록·VAPID·Service Worker](https://firebase.google.com/docs/cloud-messaging/web/get-started)
- [Firebase Admin 인증](https://firebase.google.com/docs/admin/setup)
- [Firebase Admin 7.7.0 배포 정보](https://pypi.org/project/firebase-admin/7.7.0/)
- [Railway Cron의 UTC·실행 종료 조건](https://docs.railway.com/cron-jobs)
