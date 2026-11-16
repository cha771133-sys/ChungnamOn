# FCM 운영 설정

Firebase 프로젝트는 `.firebaserc`의 `chungnamon`을 사용한다. 웹 앱은 `ChungnamOn-Web` 하나를 재사용한다.
Firebase Hosting·Firebase Authentication·Firestore는 이 웹 푸시 연동에 추가로 필요하지 않다.

## 프론트엔드

Firebase Console → 프로젝트 설정 → 일반 → 내 앱 → ChungnamOn-Web에서 웹 앱 설정을 확인한다.
프로젝트 설정 → 클라우드 메시징 → 웹 푸시 인증서에서 기존 VAPID 공개 키를 재사용한다.
실제 공개 설정값은 Git에 넣지 않고 로컬 `frontend/.env.local`과 Vercel 환경변수에 등록한다.

```dotenv
VITE_FIREBASE_API_KEY=
VITE_FIREBASE_AUTH_DOMAIN=
VITE_FIREBASE_PROJECT_ID=
VITE_FIREBASE_MESSAGING_SENDER_ID=
VITE_FIREBASE_APP_ID=
VITE_FIREBASE_VAPID_KEY=
```

Vercel → 배포 도메인 `chungnam-on.vercel.app`의 프로젝트 → Settings → Environment Variables에 등록한다.
기존 `VITE_API_BASE_URL`을 유지하고, 변경 코드 반영 후 새 빌드로 재배포한다.
`npm run build`는 `dist/firebase-messaging-sw.js`를 생성한다. 해당 파일이 배포 루트에서 HTTP 200·JavaScript로 응답해야 한다.
PWA와 FCM은 `/` 범위의 동일한 워커 등록을 공유한다.

기존 `src/lib/push.ts`의 `onRegistered`·`register`·`onUnregistered`·`unregister`를 유지한다.
FID만 기기 등록 API에 전달하며 `getToken()` 방식과 혼용하지 않는다.
알림 표시는 기존 다국어 처리기가 전담한다. SDK의 자동 알림과 별도 표시 함수를 중복 등록하지 않는다.
커스텀 `push` 처리기는 SDK보다 먼저 등록되고 이벤트 전파를 중단하므로 포그라운드·백그라운드 모두 시스템 알림은 한 번만 표시한다.

## 백엔드와 Railway

발송 계정은 `chungnamon-fcm-sender@chungnamon.iam.gserviceaccount.com`이다.
커스텀 역할 `projects/chungnamon/roles/chungnamonFcmSender`에는 `cloudmessaging.messages.create`만 부여한다.
기존 기본 Admin 서비스 계정의 권한·키는 변경하지 않는다.

Railway → Django 웹 서비스 → Variables → `FIREBASE_SERVICE_ACCOUNT_JSON`에 서비스 계정 JSON **전체**를 비밀 변수로 등록하고 배포한다.
기존 Cron 서비스가 있으면 같은 변수도 별도로 적용한다. JSON 파일 경로나 웹 앱 공개 설정을 넣지 않는다.
비공개 JSON은 저장소·프론트 빌드·로그·이슈·PR에 포함하지 않는다. 이미 발급한 키를 불필요하게 재발급하지 않는다.

`notifications/services/push_sender.py`의 기존 named app 지연 초기화·JSON 파싱을 사용한다.
설치 버전 `firebase-admin==7.7.0`은 `Message(fid=...)`를 지원한다.
FCM API, FCM Registration API, Firebase Installations API가 활성화돼 있어야 한다.

## 검증 순서

1. Vercel에서 Firebase 공개 변수 6개와 새 워커 배포를 확인한다.
2. Railway 웹/Cron 각각의 비밀 변수 적용과 재배포를 확인한다.
3. Admin SDK `messaging.send(..., dry_run=True)`로 인증과 발송 권한만 확인한다. 이는 실제 수신 테스트가 아니다.
4. `python manage.py send_deadline_reminders --dry-run --limit 10`으로 DB 후보만 확인한다. 이 명령의 dry-run은 FCM 호출도 하지 않는다.
5. 본인 테스트 계정으로 지원 브라우저에서 앱에 로그인하고 알림 권한을 허용한다. FID 기기 등록 성공을 확인한다.
6. 본인 테스트 일정의 D-7/D-1 알림을 발송하고 포그라운드·백그라운드 수신·일정 클릭 이동을 확인한다.
7. 알림을 끄면 서버 기기가 비활성화되고 후속 발송에서 제외되는지 확인한다. 로그아웃은 SDK 등록 해제도 확인한다.
8. 거부된 알림 권한, iOS 홈 화면 설치, FID 변경 시 기기 교체를 확인한다.

실제 브라우저 발급·수신·클릭을 관찰하기 전에는 송수신 완료로 판정하지 않는다.
현재 로컬 VM의 워커 처리 검사와 SDK validate_only 성공만으로 운영 수신 성공을 보증하지 않는다.

공식 문서: [웹 FCM 설정](https://firebase.google.com/docs/cloud-messaging/web/get-started),
[JavaScript FID API](https://firebase.google.com/docs/reference/js/messaging),
[Python Admin Messaging](https://firebase.google.com/docs/reference/admin/python/firebase_admin.messaging).
