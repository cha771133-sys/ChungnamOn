# ChungnamOn 프론트엔드

충남 외국인 주민을 위한 행정문서 해석·안내 서비스의 웹(PWA) 프론트엔드입니다.
React + Vite로 만들었고, 백엔드(Django) API와 연동합니다.

## 실행

Node.js 24가 필요합니다.

```powershell
cd frontend
npm install
npm run dev
```

브라우저에서 `http://localhost:5173`을 엽니다.

| 명령 | 용도 |
| --- | --- |
| `npm run dev` | 개발 서버 |
| `npm run lint` | 린트 (오류 0이어야 PR 가능) |
| `npm run build` | 타입 검사 + 빌드 (CI와 같음) |
| `npm run preview` | 빌드 결과 미리보기 (PWA 설치·서비스 워커 확인용) |

## API 주소 (`.env.local`)

API 주소는 `VITE_API_BASE_URL`로 정합니다. `.env.example`의 기본값은 배포 백엔드(Railway)입니다.

로컬 백엔드를 쓸 때는 `frontend/.env.local`을 만듭니다. 이 파일은 git에 올라가지 않습니다.

```powershell
Set-Content .env.local "VITE_API_BASE_URL=http://localhost:8000"
```

`.env.local`을 바꾼 뒤에는 개발 서버를 껐다 켜야 반영됩니다.

## 실제 폰으로 점검하기

PC에서 띄운 개발 서버를 같은 와이파이의 폰으로 엽니다. 카메라 업로드, 화면 깨짐, 키보드 가림을 확인할 때 씁니다.

1. PC의 IP 확인: `ipconfig` → "무선 LAN 어댑터 Wi-Fi"의 IPv4 주소 (아래 예시는 `192.168.0.15`)
2. `backend/.env`에 IP 추가 (`.env.example`이 아니라 `.env`)
   ```
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.0.15
   CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://192.168.0.15:5173
   ```
3. 백엔드 실행 (backend 폴더, 가상환경 켠 상태)
   ```powershell
   python manage.py migrate
   python manage.py runserver 0.0.0.0:8000
   ```
4. 프론트 실행 (frontend 폴더, 새 터미널)
   ```powershell
   Set-Content .env.local "VITE_API_BASE_URL=http://192.168.0.15:8000"
   npm run dev -- --host
   ```
5. 폰 브라우저에서 `http://192.168.0.15:5173` 접속

접속이 안 되면 관리자 PowerShell에서 방화벽을 엽니다(한 번만).

```powershell
New-NetFirewallRule -DisplayName "ChungnamOn dev" -Direction Inbound -Protocol TCP -LocalPort 5173,8000 -Action Allow -Profile Any
```

홈 화면 설치와 푸시 알림은 HTTPS에서만 동작해서 이 방법으로는 확인할 수 없습니다. 배포 주소(Vercel)에서 확인합니다.

## 다국어

- 온보딩 문구: `src/i18n.js`의 `t(lang, key)`
- 홈 이후 화면 문구: 코드에는 한국어 원문을 `T('한국어 원문')`으로 쓰고, 번역은 `src/ui-strings.js`에 넣습니다. 번역이 없으면 한국어 그대로 보입니다.
- 화면 번역이 있는 언어는 vi·en·ko입니다. 그 밖의 언어(zh·uz·ne·th)는 화면이 영어로, 문서 해석 내용은 그 언어로 나옵니다. 언어 목록에 "Menu in English"로 표시합니다.
- 상태나 오류 문구는 번역된 문장이 아니라 한국어 원문을 저장하고, 보여줄 때 `T()`로 번역합니다. 그래야 언어를 바꿨을 때 함께 바뀝니다.

## 폴더 구조

| 위치 | 내용 |
| --- | --- |
| `src/screens/` | 화면 (온보딩, 홈, 문서함, 업로드, 해석 결과, 체크리스트, 담당기관, 챗봇, 일정, 설정) |
| `src/components/` | 공통 UI, 바텀 시트 |
| `src/lib/*.ts` | API 호출 (`api.ts`가 JWT 첨부·재발급, 세션 확인 담당) |
| `src/lib/*Adapter.js` | 서버 응답 → 화면 데이터 변환 |
| `src/lib/useServer*.js` | 서버 데이터 동기화 훅 (문서, 일정, 체크리스트) |
| `src/lib/masks.js` | 개인정보 가림 표시(●, ***, `[이름 가림]` 등) 인식·번역 |
| `src/data/mock.js` | 시연용 예시 문서와 언어·국적·체류자격 목록 |
| `src/store.js` | 앱 상태, 날짜·D-day 계산, 계정별 데이터 보호 |

## 시연용 예시 문서

문서함의 예시 문서(목업)는 서버 없이도 결과·근거·체크리스트·챗봇이 모두 동작합니다. 발표 현장에서 네트워크가 불안하면 예시 문서로 시연할 수 있습니다.
