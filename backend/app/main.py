import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import db
from app.ai.provider import check_startup, get_provider
from app.api import (
    routes_auth, routes_cases, routes_drafts, routes_evidence, routes_health, routes_judgments, routes_rounds,
    routes_ocr, routes_sources, routes_support, routes_tools, routes_usage,
)
from app.config import Settings
from app.corpus.importer import fetch_pdf
from app.automation.recovery import interrupt_running_work
from app.ocr.job import interrupt_running_ocr_jobs

log = logging.getLogger(__name__)

# "testclient" 是 Starlette TestClient 的預設來源；真實連線的來源一定是 IP。
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}
# Host 標頭白名單（擋 DNS rebinding）；"testserver" 是 TestClient 的預設 Host。
ALLOWED_HOST_HEADERS = ["127.0.0.1", "localhost", "[::1]", "testserver"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


STARTUP_RECOVERY_TIMEOUT_S = 15  # 資料庫連不上時不讓啟動一直卡住（逾時後繼續啟動，工作交給心跳逾時的老辦法）


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時把上一個程序留下的辨識工作標成中斷（「繼續辨識」立刻出現）；失敗只記錄，不擋啟動。
    這要用超級使用者連線（跨事務所、RLS 擋掉一般帳號），網頁伺服器不該持有它，所以只在開發環境執行；
    試用與正式環境維持原本的 10 分鐘心跳逾時，改由計畫 5 的單一協調者處理（見 docs/deploy-notes.md）。"""
    if app.state.settings.app_env == "dev":
        try:
            n = await asyncio.wait_for(run_in_threadpool(interrupt_running_ocr_jobs), STARTUP_RECOVERY_TIMEOUT_S)
            if n:
                log.warning("啟動時把 %d 筆上次留下的辨識工作標成中斷", n)
        except Exception:
            log.exception("啟動時標記中斷的辨識工作失敗；這些工作要等心跳逾時才會出現「繼續辨識」")
        try:  # 輪次階段與其他 AI 工作也一樣：自動排定的階段下一次打開頁面時會自動重跑一次（設計 6.2）
            rounds, jobs = await asyncio.wait_for(run_in_threadpool(interrupt_running_work),
                                                  STARTUP_RECOVERY_TIMEOUT_S)
            if rounds or jobs:
                log.warning("啟動時把 %d 個輪次階段、%d 筆 AI 工作標成中斷", rounds, jobs)
        except Exception:
            log.exception("啟動時標記中斷的輪次與 AI 工作失敗；這些要等逾時才會出現「重試」")
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    check_startup(settings)
    db.configure(settings)
    app = FastAPI(title="legaltech", lifespan=lifespan)
    app.state.settings = settings
    app.state.provider_factory = get_provider  # 測試可換成假的供應來源
    app.state.judgment_fetcher = fetch_pdf  # 向司法院下載判決 PDF；測試換成假的，不連網
    app.include_router(routes_auth.router)
    app.include_router(routes_cases.router)
    app.include_router(routes_rounds.router)
    app.include_router(routes_drafts.router)
    app.include_router(routes_sources.router)
    app.include_router(routes_evidence.router)
    app.include_router(routes_health.router)
    app.include_router(routes_judgments.router)
    app.include_router(routes_tools.router)
    app.include_router(routes_usage.router)
    app.include_router(routes_ocr.router)
    app.include_router(routes_support.router)

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host = request.client.host if request.client else None
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "只允許本機存取"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """擋跨站請求偽造：瀏覽器送寫入請求時一定帶 Origin，不在允許清單就拒絕（含本機其他埠、"null"）。"""
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin is not None and origin not in settings.allowed_origins:
            return JSONResponse({"detail": "拒絕跨站請求"}, status_code=403)
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOST_HEADERS)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
