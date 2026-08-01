import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
