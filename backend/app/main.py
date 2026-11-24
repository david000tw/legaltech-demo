from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.ai.provider import check_startup, get_provider
from app.api import routes_auth, routes_cases, routes_drafts, routes_rounds, routes_sources
from app.config import Settings

# "testclient" 是 Starlette TestClient 的預設來源；真實連線的來源一定是 IP。
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}
# Host 標頭白名單（擋 DNS rebinding）；"testserver" 是 TestClient 的預設 Host。
ALLOWED_HOST_HEADERS = ["127.0.0.1", "localhost", "[::1]", "testserver"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    check_startup(settings)
    db.configure(settings)
    app = FastAPI(title="legaltech")
    app.state.settings = settings
    app.state.provider_factory = get_provider  # 測試可換成假的供應來源
    app.include_router(routes_auth.router)
    app.include_router(routes_cases.router)
    app.include_router(routes_rounds.router)
    app.include_router(routes_drafts.router)
    app.include_router(routes_sources.router)

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
