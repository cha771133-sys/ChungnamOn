# ChungnamOn

서비스 기능과 작업 규칙은 @docs/SERVICE_CONTEXT.md 를 참고한다.

## 핵심 작업 규칙

- 브랜치는 `dev`에서 `feature/...`·`fix/...`·`docs/...`로 만들고, PR 대상은 `dev`다.
- `main`·`dev`에 직접 push하지 않는다. force push 금지, `main`·`dev` 삭제 금지.
- 머지는 차성진만 한다. Claude Code는 PR 생성까지만 하고 머지하지 않는다.
- `.env`·비밀키는 커밋하지 않는다. 프론트 `VITE_` 변수에 API 키를 넣지 않는다.
- 담당이 아닌 Django 앱은 수정하지 않는다 (documents = 차성진, accounts·sources·chatbot·공통 = B).
