# 협업 규칙

## 🌿 브랜치 전략

### 브랜치 구조

작업 브랜치 → `dev`(중간 통합) → `main`(최종 제출·배포) 순서로 올라갑니다.

```
main      ← 최종 제출·데모 배포용 (항상 동작하는 상태)
 └ dev    ← 중간 통합용 · 저장소 기본 브랜치
    ├ feature/...   기능 개발
    ├ fix/...       버그 수정
    ├ docs/...      문서 작업
    └ chore/...     설정·CI·패키지
```

| 브랜치 | 용도 | 들어오는 방법 |
| --- | --- | --- |
| `main` | 최종 제출·실제 배포 (데모 주소) | `dev`에서 올린 PR만 · **승인 1명** + CI 통과 |
| `dev` | 중간 통합·테스트 (테스트 배포 주소), **기본 브랜치** | 작업 브랜치에서 PR · **승인 없이** CI 통과만 하면 바로 merge |
| `feature/*` 등 | 작업 브랜치 | 본인 브랜치에 직접 push |

- 모든 작업 브랜치는 **반드시 `dev`에서 생성**합니다.
- `main`과 `dev`에는 직접 push할 수 없고, 삭제·force push도 막혀 있습니다.
- `main`에 `dev`가 아닌 브랜치로 PR을 올리면 `main-source-check` 체크가 실패해 merge할 수 없습니다.

### dev에 merge하기 전 로컬 확인

`dev`는 승인 없이 merge되므로, **PR을 올리기 전에 본인이 로컬에서 빌드·테스트를 통과시킵니다.** CI도 같은 검사를 다시 합니다.

```bash
# 백엔드 (backend/에서, 가상환경 활성화 후)
python manage.py makemigrations --check --dry-run
python manage.py test

# 프론트 (frontend/에서)
npm run lint
npm run build
```

### 브랜치 이름 규칙

`종류/영역-작업내용` (소문자, 하이픈 사용)

- 영역: `fe`(프론트), `be`(백엔드), `ai`(AI)
- 이슈 번호를 붙이면 추적이 쉬워요: `feature/be-document-api-12`

| 예시 | 의미 |
| --- | --- |
| `feature/fe-upload-page` | 프론트 업로드 화면 |
| `feature/be-document-api` | 백엔드 문서 업로드 API |
| `feature/ai-ocr-pipeline` | AI OCR 처리 |
| `fix/be-cors-error` | 백엔드 CORS 오류 수정 |
| `docs/readme-setup` | README 실행 방법 정리 |

### 작업 순서

```bash
# 1. dev 최신화
git checkout dev
git pull origin dev

# 2. dev에서 작업 브랜치 생성
git checkout -b feature/be-document-api

# 3. 로컬에서 빌드·테스트 확인 후 커밋
git add .
git commit -m "feat: 문서 업로드 API 추가"

# 4. push
git push origin feature/be-document-api

# 5. GitHub에서 PR 생성 (대상 브랜치는 기본값인 dev)

# 6. CI 통과 후 PR 작성자가 Squash and merge
```

---

## 🔀 PR 규칙

### 기본 원칙

- **방향:** 작업 브랜치 → `dev` / 데모 전에만 `dev` → `main`
- **크기:** PR 하나에 기능 하나
- **PR 제목:** 커밋 규칙과 같게 작성 (예: `feat: 문서 업로드 API 추가`). Squash merge 시 PR 제목이 그대로 커밋 메시지가 됩니다.

### merge 방식

| PR 방향 | merge 방식 | 이유 |
| --- | --- | --- |
| 작업 브랜치 → `dev` | **Squash and merge** | 기능 하나를 커밋 하나로 정리 |
| `dev` → `main` | **Create a merge commit** | Squash하면 `main`과 `dev` 이력이 어긋나 다음 merge 때 충돌 발생 |

### 머지

- 작업 브랜치 → `dev`: **CI 통과** 후 **PR 작성자가 직접** Squash and merge합니다. 승인은 필요 없습니다.
- `dev` → `main`: 데모 전에 **차성진**이 PR을 올리고, **승인 1명 + CI 통과** 후 merge합니다.
- CI가 실패한 PR은 merge할 수 없습니다.

### 리뷰

- `dev` PR은 승인 없이 merge할 수 있지만, 다른 팀원 코드와 맞물리는 변경은 리뷰를 요청하는 것을 권장합니다.
- `main` PR은 작성자가 아닌 팀원 **1명 이상의 승인**이 필요합니다.
- 리뷰 요청을 받으면 **4시간 안에** 리뷰를 남깁니다. 늦어지면 팀 채팅에 알립니다.
- CodeRabbit 자동 리뷰는 1차 확인용입니다. **사람 리뷰를 대신하지 않습니다.**

### CI

`dev`, `main`으로 PR을 올리면 GitHub Actions가 자동으로 검사합니다. 아래 체크가 모두 ✅여야 merge할 수 있습니다.

| 체크 | 대상 | 검사 내용 |
| --- | --- | --- |
| `backend-test` | `dev`, `main` | 마이그레이션 체크(`makemigrations --check`) + 테스트 |
| `frontend-build` | `dev`, `main` | lint + build |
| `main-source-check` | `main` | PR이 `dev`에서 올라왔는지 확인 |

- `backend-test`, `frontend-build`는 항상 실행되지만, 해당 영역(`backend/`, `frontend/`) 변경이 없으면 검사 단계를 건너뛰고 통과합니다.
- CI가 실패하면 Checks 탭에서 로그를 확인하고 수정한 뒤 다시 push합니다.
- 모델을 바꿨다면 `python manage.py makemigrations`로 만든 마이그레이션 파일을 함께 커밋해야 CI를 통과합니다.

### CodeRabbit

PR이 올라오면 CodeRabbit이 변경된 코드를 분석해 리뷰 코멘트를 자동으로 남깁니다.

- 코멘트를 확인하고 반영하거나, 반영하지 않을 경우 이유를 답글로 남깁니다.
- PR 코멘트에서 `@coderabbitai`를 언급하면 추가 질문을 할 수 있습니다.

### PR 올릴 때

1. Assignees에 본인 지정 (리뷰가 필요하면 Reviewers에 팀원 지정)
2. 템플릿에 관련 이슈를 `close #이슈번호`로 적기 → `dev`에 merge되면 이슈가 자동으로 닫힘
3. 팀 채팅에 PR 링크 공유
4. CI 결과와 CodeRabbit 코멘트 확인
5. 충돌이 생기면 **PR 작성자**가 `dev`를 pull 받아 해결한 뒤 다시 push (Django 마이그레이션 충돌은 `python manage.py makemigrations --merge`로 해결)
6. CI 통과를 확인한 뒤 **PR 작성자가 Squash and merge**

### PR 템플릿

`.github/pull_request_template.md`에 저장되어 있어 PR을 만들 때 자동으로 채워집니다.

```markdown
## 작업 내용
- 

## 관련 이슈
- close #

## 테스트 방법
- 

## 스크린샷 (화면 작업인 경우)
```

---

## 📝 커밋 메시지 규칙

`타입: 내용`

| 타입 | 용도 | 예시 |
| --- | --- | --- |
| `feat` | 새 기능 | `feat: 체크리스트 생성 API 추가` |
| `fix` | 버그 수정 | `fix: 업로드 용량 제한 오류 수정` |
| `refactor` | 동작 변화 없는 코드 정리 | `refactor: 해석 결과 serializer 분리` |
| `style` | UI 스타일, 코드 포맷 | `style: 결과 화면 여백 조정` |
| `docs` | 문서 | `docs: README 실행 방법 추가` |
| `chore` | 설정, 패키지 | `chore: whitenoise 설치` |
| `test` | 테스트 코드 | `test: 업로드 API 테스트 추가` |

---

## 🔒 보안

- `.env` 파일은 커밋하지 않습니다. 필요한 변수는 값 없이 `.env.example`에만 추가합니다.
