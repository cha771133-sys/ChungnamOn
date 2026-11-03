# ChungnamOn

React(Vite + TypeScript) 프론트엔드와 Django(DRF) 백엔드로 구성된 프로젝트입니다.

## 요구 사항

- Python 3.14
- Node.js 24

## 백엔드 실행

Windows PowerShell 기준입니다.

기존 DB가 있다면 `migrate` 전에 [인증 사용자 모델 전환 주의사항](backend/accounts/README.md#기존-db-전환-차단-사항)을 확인하세요. 기본 User로 `admin` 마이그레이션을 적용한 DB는 별도 전환이 필요합니다.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

`.env`의 `DJANGO_SECRET_KEY`는 임의의 긴 문자열로 바꿔 주세요.

## 프론트엔드 실행

```powershell
cd frontend
npm install
npm run dev
```

브라우저에서 http://localhost:5173 을 열면 됩니다. 개발 서버는 `/api` 요청을 `http://127.0.0.1:8000`으로 프록시합니다.

## 테스트

```powershell
cd backend
python manage.py test
```

```powershell
cd frontend
npm run lint
npm run build
```

## 협업 규칙

브랜치, PR, 커밋 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.
