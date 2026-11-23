from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.ai.provider import check_startup, get_provider
from app.api import routes_auth, routes_cases, routes_rounds
from app.config import Settings

# "testclient" 是 Starlette TestClient 的預設來源；真實連線的來源一定是 IP。
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}
# Host 標頭白名單（擋 DNS rebinding）；"testserver" 是 TestClient 的預設 Host。
ALLOWED_HOST_HEADERS = ["127.0.0.1", "localhost", "[::1]", "testserver"]


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

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host = request.client.host if request.client else None
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "只允許本機存取"}, status_code=403)
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOST_HEADERS)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
