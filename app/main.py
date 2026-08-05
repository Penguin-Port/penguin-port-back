import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import init_db, session_scope
from app.routers import admin, pos, public
from app.services.wifi import expire_due_passes


async def _expire_loop():
    while True:
        with session_scope() as db:
            expire_due_passes(db)
        await asyncio.sleep(settings.expire_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(_expire_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Smart WiFi Pass MVP",
    version="1.0.0",
    description="PDF 축소 명세의 FastAPI 단일 백엔드입니다.",
    lifespan=lifespan,
)


def _problem_code(status: int, detail) -> str:
    if isinstance(detail, dict) and detail.get("code"):
        return str(detail["code"])
    if isinstance(detail, str):
        normalized = "".join(
            character if character.isalnum() else "_" for character in detail.upper()
        ).strip("_")
        if normalized and len(normalized) <= 80 and normalized.count("_") >= 1:
            return normalized
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        410: "GONE",
        422: "DOMAIN_RULE_VIOLATION",
        429: "RATE_LIMITED",
    }.get(status, "INTERNAL_SERVER_ERROR")


def _problem_response(request: Request, *, status: int, detail, headers=None) -> JSONResponse:
    code = _problem_code(status, detail)
    message = detail.get("detail", "요청을 처리할 수 없습니다.") if isinstance(detail, dict) else detail
    request_id = request.headers.get("X-Request-Id", f"req_{uuid4().hex}")
    body = {
        "type": f"https://api.example.com/problems/{code.lower()}",
        "title": code.replace("_", " ").title(),
        "status": status,
        "code": code,
        "detail": message,
        "retryable": status == 429 or status >= 500,
        "requestId": request_id,
    }
    return JSONResponse(status_code=status, content=body, headers=headers)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return _problem_response(request, status=exc.status_code, detail=exc.detail, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _problem_response(
        request,
        status=422,
        detail={"code": "REQUEST_VALIDATION_ERROR", "detail": exc.errors()},
    )

for router in (pos.router, public.router, admin.router):
    app.include_router(router)
    # 기존 프론트의 /api/v1 base URL도 전환 기간 동안 지원한다.
    app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
