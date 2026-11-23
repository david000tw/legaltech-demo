"""唯一的啟動方式：python -m app.serve（監聽位址取自 Settings.bind_host）。"""
import uvicorn

from app.config import Settings
from app.main import create_app


def main() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.port,
                proxy_headers=False)  # 不信任 X-Forwarded-For，避免外部請求偽裝成本機


if __name__ == "__main__":
    main()
