from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import db
from app.api import routes_auth
from app.config import Settings

# "testclient" 是 Starlette TestClient 的預設來源；真實連線的來源一定是 IP。
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db.configure(settings)
    app = FastAPI(title="legaltech")
    app.state.settings = settings
    app.include_router(routes_auth.router)

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        host = request.client.host if request.client else None
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "只允許本機存取"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
