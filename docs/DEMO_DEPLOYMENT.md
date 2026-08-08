# MVP 시연 배포 환경 변수

이 저장소의 MVP 시연 API는 FastAPI 서비스(`8000`)를 기준으로 합니다. Django full profile을
함께 실행하는 경우에도 같은 OpenAI 환경변수를 서비스와 worker가 읽도록 등록합니다.

## 반드시 등록할 값

배포 서비스의 `Environment`, `Secrets` 또는 `Config Vars` 화면에 아래 값을 등록합니다.

| 변수 | 예시 | 용도 |
| --- | --- | --- |
| `OPENAI_API_KEY` | `sk-...` | 서버에서 OpenAI Responses API를 호출하는 비밀 키 |
| `OPENAI_MODEL` | `gpt-5-mini` | 추천 생성에 사용할 모델 이름 |
| `OPENAI_TIMEOUT_SECONDS` | `20` | OpenAI 호출 제한 시간 |
| `CORS_ALLOWED_ORIGINS` | `https://frontend.example.com` | 브라우저에서 호출할 배포 프론트 Origin |
| `SECURE_COOKIES` | `1` | HTTPS·교차 사이트 SSE 쿠키 인증 사용 시 필요 |
| `REDIS_URL` | `redis://...` | 여러 backend 프로세스 간 SSE fan-out |
| `USE_CELERY` | `1` | 배포 환경의 만료·정리 작업을 Celery로 실행 |

`CORS_ALLOWED_ORIGINS`는 쉼표로 구분한 정확한 Origin 목록이며 경로(`/`)를 붙이지 않습니다.
예를 들어 `https://frontend.example.com,https://www.frontend.example.com`처럼 등록합니다.
인증 쿠키를 사용하므로 `*`는 사용하지 않습니다.

## 등록 순서

1. OpenAI Platform에서 API 키를 생성합니다. 키는 서버 환경변수나 Secret Manager에만 저장하고
   프론트엔드 코드, Git, 로그에 넣지 않습니다. [OpenAI API quickstart](https://platform.openai.com/docs/quickstart/make-your-first-api-request)
   도 서버 환경변수 사용을 안내합니다.
2. 배포 중인 backend 서비스의 환경변수 화면에 위 값을 등록합니다. `OPENAI_API_KEY`는
   Secret 타입으로 저장하고, `OPENAI_MODEL`은 `gpt-5-mini`로 지정합니다.
3. 환경변수는 프로세스 시작 시 읽으므로 저장 후 backend와 worker를 재배포합니다.
4. 관리자 로그인 후 추천 생성 API를 호출해 `payload.source`가 `OPENAI`인지 확인합니다.

```bash
curl -X POST https://api.example.com/admin/ai/recommendations/generate \
  -H 'Authorization: Bearer ADMIN_ACCESS_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"storeId":"STORE_UUID","type":"TIME_SALE"}'
```

`payload.source`가 `RULE_FALLBACK`이면 키 미등록, 모델 호출 실패, 또는 응답 검증 실패 상태입니다.
이 경우에도 시연 API는 동작하지만 실제 OpenAI 생성 결과 시연은 아닙니다.

## SSE와 교차 사이트 쿠키

배포 프론트가 backend와 다른 사이트인 경우 다음을 함께 설정합니다.

```dotenv
CORS_ALLOWED_ORIGINS=https://frontend.example.com
SECURE_COOKIES=1
```

로그인 요청은 `credentials: "include"`로 보내고, native EventSource는 다음처럼 연결합니다.

```javascript
const stream = new EventSource(`${API_URL}/admin/events`, {
  withCredentials: true,
});
```

Bearer 헤더를 직접 지정할 수 있는 fetch 기반 SSE 클라이언트라면 로그인 응답의 `accessToken`을
`Authorization: Bearer ...`로 전달해도 됩니다.

## 보안 주의

- `OPENAI_API_KEY`를 `.env.example`, 프론트 번들, 이슈, 채팅, 커밋에 실제 값으로 넣지 않습니다.
- 키가 노출되면 즉시 OpenAI Platform에서 폐기하고 새 키로 교체합니다.
- 배포 프론트 Origin을 추가하거나 변경하면 `CORS_ALLOWED_ORIGINS`를 갱신하고 재배포합니다.
